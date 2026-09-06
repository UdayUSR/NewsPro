import os
import sys
import json
import re
import html
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
import requests
from bs4 import BeautifulSoup
from google import genai
from google.genai import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from database.db import save_article, CANONICAL_CATEGORIES, get_all_ingested_urls, is_title_duplicate, get_all_existing_headlines

load_dotenv()
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

SESSION = requests.Session()
SESSION.headers.update({
    'User-Agent': 'facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Accept-Language': 'bn,en-US;q=0.9,en;q=0.8'
})

CATEGORIES_LIST = ", ".join(f'"{c[0]}"' for c in CANONICAL_CATEGORIES if c[1] != "latest")

def extract_from_html(resp_text, url):
    """Extracts headline, clean comprehensive body, and lead image from HTML."""
    soup = BeautifulSoup(resp_text, 'html.parser')

    # 1. Headline
    headline = ""
    h1 = soup.find('h1')
    if h1 and len(h1.get_text().strip()) > 10:
        headline = h1.get_text().strip()

    ld_headline = ""
    ld_body = ""
    ld_image = ""
    for s in soup.find_all('script', type='application/ld+json'):
        try:
            if not s.string: continue
            data = json.loads(s.string)
            items = data if isinstance(data, list) else [data]
            for it in items:
                if it.get('@type') in ['NewsArticle', 'Article', 'ReportageNewsArticle']:
                    if not ld_headline and it.get('headline'):
                        ld_headline = it.get('headline').strip()
                    b = it.get('articleBody') or ''
                    if '&lt;' in b or '<' in b:
                        b = BeautifulSoup(html.unescape(b), 'html.parser').get_text(separator=' ').strip()
                    if len(b) > len(ld_body):
                        ld_body = b.strip()
                    if not ld_image and it.get('image'):
                        img_val = it.get('image')
                        if isinstance(img_val, str):
                            ld_image = img_val
                        elif isinstance(img_val, dict) and img_val.get('url'):
                            ld_image = img_val.get('url')
                        elif isinstance(img_val, list) and img_val and isinstance(img_val[0], str):
                            ld_image = img_val[0]
        except Exception:
            pass

    if not headline:
        headline = ld_headline

    # 2. Extract paragraph text
    article_elem = soup.find('article') or soup.find(class_=re.compile(r'news-content|details-content|article-content|story-content|content-details'))
    search_root = article_elem if article_elem else soup

    paragraphs = []
    for p in search_root.find_all('p'):
        text = p.get_text().strip()
        if len(text) > 35 and not any(k in text for k in ['বিজ্ঞাপন', 'সর্বস্বত্ব সংরক্ষিত', 'কপিরাইট', 'ঢাকা, বাংলাদেশ', 'প্রকাশিত:']):
            paragraphs.append(text)
    p_body = ' '.join(paragraphs).strip()

    # Intelligent body selection:
    # If ld_body is >= 400 chars, it is usually clean editorial body (e.g. Kaler Kantho).
    # If ld_body is small (< 400 chars, e.g. Janakantha lead summary) and p_body is much longer, use p_body.
    if len(ld_body) >= 400:
        body = ld_body
    elif len(p_body) >= 150:
        body = p_body
    elif ld_body:
        body = ld_body
    else:
        body = p_body

    # 3. Lead image extraction
    image_url = ""
    og_img = soup.find('meta', property='og:image') or soup.find('meta', attrs={'name': 'og:image'})
    if og_img and og_img.get('content'):
        image_url = og_img.get('content').strip()
    if not image_url:
        image_url = ld_image

    if headline and len(body) > 80:
        return headline, body, image_url
    return None, None, None

def fetch_prothom_alo(limit=5, existing_urls=None):
    existing = existing_urls or set()
    articles = []
    candidate_urls = []

    # 1. Front Homepage and Top Topic Sections (captures lead features, breaking national & world news)
    sections = ['', '/world', '/bangladesh', '/politics']
    for sec in sections:
        try:
            r = SESSION.get('https://www.prothomalo.com' + sec, timeout=15)
            soup = BeautifulSoup(r.text, 'html.parser')
            for a in soup.find_all('a', href=True):
                raw_href = a['href'].strip()
                p = raw_href.replace('https://www.prothomalo.com', '')
                if re.match(r'^/(?:bangladesh|world|business|politics|opinion|sports|entertainment|education|technology)/[a-z0-9-]+/[a-z0-9]{8,}$', p):
                    full = f"https://www.prothomalo.com{p}"
                    if full not in candidate_urls and full not in existing:
                        candidate_urls.append(full)
        except Exception as e:
            print(f"  [!] Prothom Alo page error ({sec}): {e}")

    # 2. Stories API & Collections API for broad depth
    try:
        r2 = SESSION.get('https://www.prothomalo.com/api/v1/stories?offset=0&limit=30', timeout=15)
        if r2.status_code == 200:
            for s in r2.json().get('stories', []):
                slug = s.get('slug')
                if slug:
                    u = f"https://www.prothomalo.com/{slug}"
                    if u not in candidate_urls and u not in existing:
                        candidate_urls.append(u)
        r1 = SESSION.get('https://www.prothomalo.com/api/v1/collections/latest', timeout=15)
        if r1.status_code == 200:
            for item in r1.json().get('items', []):
                slug = item.get('story', {}).get('slug')
                if slug:
                    u = f"https://www.prothomalo.com/{slug}"
                    if u not in candidate_urls and u not in existing:
                        candidate_urls.append(u)
    except Exception as e:
        print(f"  [!] Prothom Alo API scan error: {e}")

    for url in candidate_urls:
        if len(articles) >= limit: break
        try:
            art_r = SESSION.get(url, timeout=15)
            h, body, img = extract_from_html(art_r.text, url)
            if body:
                articles.append({
                    'source': 'প্রথম আলো',
                    'title': h,
                    'url': url,
                    'body': body,
                    'lead_image_url': img or ''
                })
        except Exception:
            pass
    return articles

def fetch_kaler_kantho(limit=5, existing_urls=None):
    existing = existing_urls or set()
    articles = []
    candidate_urls = []
    sections = ['', '/online/national', '/online/politics', '/online/business']
    for sec in sections:
        try:
            r = SESSION.get('https://www.kalerkantho.com' + sec, timeout=15)
            soup = BeautifulSoup(r.text, 'html.parser')
            for a in soup.find_all('a', href=True):
                href = a['href']
                if any(c.isdigit() for c in href) and any(k in href for k in ['/online/', '/national/', '/country-news/', '/politics/']):
                    full = href if href.startswith('http') else f"https://www.kalerkantho.com{href}"
                    if full not in candidate_urls and full not in existing:
                        candidate_urls.append(full)
        except Exception:
            pass

    for url in candidate_urls:
        if len(articles) >= limit: break
        try:
            art_r = SESSION.get(url, timeout=15)
            h, body, img = extract_from_html(art_r.text, url)
            if body:
                articles.append({
                    'source': 'কালের কণ্ঠ',
                    'title': h,
                    'url': url,
                    'body': body,
                    'lead_image_url': img or ''
                })
        except Exception:
            pass
    return articles

def fetch_jugantor(limit=5, existing_urls=None):
    existing = existing_urls or set()
    articles = []
    candidate_urls = []
    sections = ['', '/national', '/politics', '/economics']
    for sec in sections:
        try:
            r = SESSION.get('https://www.jugantor.com' + sec, timeout=15)
            soup = BeautifulSoup(r.text, 'html.parser')
            for a in soup.find_all('a', href=True):
                href = a['href']
                if re.search(r'/(?:national|politics|sports|country-news|lifestyle|capital|economics|world|entertainment|education)/\d+', href):
                    full = href if href.startswith('http') else f"https://www.jugantor.com{href}"
                    if full not in candidate_urls and full not in existing:
                        candidate_urls.append(full)
        except Exception:
            pass

    for url in candidate_urls:
        if len(articles) >= limit: break
        try:
            art_r = SESSION.get(url, timeout=15)
            h, body, img = extract_from_html(art_r.text, url)
            if body:
                articles.append({
                    'source': 'যুগান্তর',
                    'title': h,
                    'url': url,
                    'body': body,
                    'lead_image_url': img or ''
                })
        except Exception:
            pass
    return articles

def fetch_janakantha(limit=5, existing_urls=None):
    existing = existing_urls or set()
    articles = []
    candidate_urls = []
    sections = ['', '/bangladesh', '/politics', '/world', '/economics']
    for sec in sections:
        try:
            r = SESSION.get('https://www.dailyjanakantha.com' + sec, timeout=15)
            soup = BeautifulSoup(r.text, 'html.parser')
            for a in soup.find_all('a', href=True):
                href = a['href']
                if any(c.isdigit() for c in href) and any(k in href for k in ['/news/', '/bangladesh/', '/politics/', '/world/']):
                    full = href if href.startswith('http') else f"https://www.dailyjanakantha.com{href}"
                    if full not in candidate_urls and full not in existing:
                        candidate_urls.append(full)
        except Exception:
            pass

    for url in candidate_urls:
        if len(articles) >= limit: break
        try:
            art_r = SESSION.get(url, timeout=15)
            h, body, img = extract_from_html(art_r.text, url)
            if body:
                articles.append({
                    'source': 'জনকণ্ঠ',
                    'title': h,
                    'url': url,
                    'body': body,
                    'lead_image_url': img or ''
                })
        except Exception:
            pass
    return articles

def fetch_tbs_bangla(limit=5, existing_urls=None):
    existing = existing_urls or set()
    articles = []
    candidate_urls = []
    sections = ['/bangla', '/bangla/bangladesh', '/bangla/Economy']
    for sec in sections:
        try:
            r = SESSION.get('https://www.tbsnews.net' + sec, timeout=15)
            soup = BeautifulSoup(r.text, 'html.parser')
            for a in soup.find_all('a', href=True):
                href = a['href']
                if 'news-details' in href:
                    full = href if href.startswith('http') else f"https://www.tbsnews.net{href}"
                    if full not in candidate_urls and full not in existing:
                        candidate_urls.append(full)
        except Exception:
            pass

    for url in candidate_urls:
        if len(articles) >= limit: break
        try:
            art_r = SESSION.get(url, timeout=15)
            h, body, img = extract_from_html(art_r.text, url)
            if body:
                articles.append({
                    'source': 'টিবিএস বাংলা',
                    'title': h,
                    'url': url,
                    'body': body,
                    'lead_image_url': img or ''
                })
        except Exception:
            pass
    return articles

def fetch_dhaka_tribune_bangla(limit=5, existing_urls=None):
    existing = existing_urls or set()
    articles = []
    candidate_urls = []
    sections = ['', '/bangladesh', '/politics', '/court']
    for sec in sections:
        try:
            r = SESSION.get('https://bangla.dhakatribune.com' + sec, timeout=15)
            soup = BeautifulSoup(r.text, 'html.parser')
            for a in soup.find_all('a', href=True):
                href = a['href']
                if any(k in href for k in ['/bangladesh/', '/politics/', '/sport/', '/court/', '/world/', '/business/']) and any(c.isdigit() for c in href):
                    if href.startswith('http'):
                        full = href
                    elif href.startswith('//'):
                        full = 'https:' + href
                    else:
                        full = 'https://bangla.dhakatribune.com/' + href.lstrip('/')
                    if full not in candidate_urls and full not in existing:
                        candidate_urls.append(full)
        except Exception:
            pass

    for url in candidate_urls:
        if len(articles) >= limit: break
        try:
            art_r = SESSION.get(url, timeout=15)
            h, body, img = extract_from_html(art_r.text, url)
            if body:
                articles.append({
                    'source': 'ঢাকা ট্রিবিউন',
                    'title': h,
                    'url': url,
                    'body': body,
                    'lead_image_url': img or ''
                })
        except Exception:
            pass
    return articles

def fetch_banglanews24(limit=5, existing_urls=None):
    existing = existing_urls or set()
    articles = []
    candidate_urls = []
    sections = ['', '/national', '/politics', '/economics']
    for sec in sections:
        try:
            r = SESSION.get('https://www.banglanews24.com' + sec, timeout=15)
            soup = BeautifulSoup(r.text, 'html.parser')
            for a in soup.find_all('a', href=True):
                href = a['href'].strip()
                if ('news/bd/' in href or '.details' in href) and any(c.isdigit() for c in href):
                    full = href if href.startswith('http') else f"https://www.banglanews24.com{href}"
                    if 'banglanews24.com' in full and full not in candidate_urls and full not in existing:
                        candidate_urls.append(full)
        except Exception:
            pass

    for url in candidate_urls:
        if len(articles) >= limit: break
        try:
            art_r = SESSION.get(url, timeout=15)
            h, body, img = extract_from_html(art_r.text, url)
            if body:
                articles.append({
                    'source': 'বাংলানিউজ২৪',
                    'title': h,
                    'url': url,
                    'body': body,
                    'lead_image_url': img or ''
                })
        except Exception:
            pass
    return articles

def fetch_dhaka_post(limit=5, existing_urls=None):
    existing = existing_urls or set()
    articles = []
    candidate_urls = []
    sections = ['', '/national', '/politics', '/economy']
    for sec in sections:
        try:
            r = SESSION.get('https://www.dhakapost.com' + sec, timeout=15)
            soup = BeautifulSoup(r.text, 'html.parser')
            for a in soup.find_all('a', href=True):
                href = a['href'].strip()
                if any(sec_tag in href for sec_tag in ['/national/', '/politics/', '/economy/', '/country/', '/international/', '/sports/']) and any(c.isdigit() for c in href):
                    full = href if href.startswith('http') else f"https://www.dhakapost.com{href}"
                    if 'dhakapost.com' in full and full not in candidate_urls and full not in existing:
                        candidate_urls.append(full)
        except Exception:
            pass

    for url in candidate_urls:
        if len(articles) >= limit: break
        try:
            art_r = SESSION.get(url, timeout=15)
            h, body, img = extract_from_html(art_r.text, url)
            if body:
                articles.append({
                    'source': 'ঢাকা পোস্ট',
                    'title': h,
                    'url': url,
                    'body': body,
                    'lead_image_url': img or ''
                })
        except Exception:
            pass
    return articles

def cluster_articles(all_articles):
    stopwords = set([
        'ও', 'এবং', 'থেকে', 'করা', 'হয়েছে', 'হল', 'হলো', 'করে', 'বা', 'না', 
        'নিয়ে', 'যা', 'হচ্ছে', 'এই', 'এক', 'দুই', 'তিন', 'চার', 'পাঁচ', 'ছয়', 
        'লাখ', 'কোটি', 'টাকা', 'পর', 'কী', 'কে', 'দিয়ে', 'জন্য', 'বড়', 'হতে',
        'বলে', 'জানান', 'জানানো', 'থাকেন', 'হবে', 'তিনি', 'তারা', 'তাঁর', 'তার',
        'এর', 'সে', 'সেই', 'আজ', 'কাল', 'গতকাল', 'পরে', 'আগে', 'নতুন', 'সঙ্গে',
        'দিয়েছেন', 'বলেন', 'করছেন', 'করতে', 'সব'
    ])
    def tokenize(text):
        words = re.findall(r'[\u0980-\u09FF]+', text)
        return set(w for w in words if len(w) > 2 and w not in stopwords)

    clusters = []
    used_indices = set()
    n = len(all_articles)

    for i in range(n):
        if i in used_indices: continue
        cluster = [all_articles[i]]
        sources_in_cluster = {all_articles[i]['source']}
        
        title_i = tokenize(all_articles[i]['title'])
        lead_i = tokenize(all_articles[i]['body'][:350] if all_articles[i].get('body') else '')
        combined_i = title_i.union(lead_i)

        for j in range(i + 1, n):
            if j in used_indices: continue
            if all_articles[j]['source'] in sources_in_cluster: continue
            
            title_j = tokenize(all_articles[j]['title'])
            lead_j = tokenize(all_articles[j]['body'][:350] if all_articles[j].get('body') else '')
            combined_j = title_j.union(lead_j)
            
            title_overlap = title_i.intersection(title_j)
            combined_overlap = combined_i.intersection(combined_j)
            
            title_union = title_i.union(title_j)
            title_jaccard = len(title_overlap) / len(title_union) if title_union else 0
            
            combined_union = combined_i.union(combined_j)
            combined_jaccard = len(combined_overlap) / len(combined_union) if combined_union else 0

            # Match criteria:
            # 1. 3+ title words overlap
            # 2. 2+ title words overlap AND Jaccard >= 0.22
            # 3. 2+ title words overlap AND 5+ lead entity words overlap
            # 4. 1+ title word overlap AND 7+ lead entity overlap AND combined Jaccard >= 0.15
            is_match = False
            if len(title_overlap) >= 3:
                is_match = True
            elif len(title_overlap) >= 2 and (title_jaccard >= 0.22 or len(combined_overlap) >= 5):
                is_match = True
            elif len(title_overlap) >= 1 and len(combined_overlap) >= 7 and combined_jaccard >= 0.15:
                is_match = True

            if is_match:
                cluster.append(all_articles[j])
                sources_in_cluster.add(all_articles[j]['source'])
                used_indices.add(j)

        if len(cluster) > 1:
            used_indices.add(i)
            clusters.append(cluster)

    # Also collect unclustered single-source articles
    unclustered = [all_articles[i] for i in range(n) if i not in used_indices]
    return clusters, unclustered


def call_gemini(prompt):
    candidate_models = [
        "gemini-3.6-flash",
        "gemini-3.5-flash-lite",
        "gemini-3.1-flash-lite",
        "gemini-flash-latest",
        "gemini-3.5-flash"
    ]
    for m in candidate_models:
        try:
            resp = client.models.generate_content(
                model=m,
                contents=prompt,
                config=types.GenerateContentConfig(response_mime_type="application/json")
            )
            data = json.loads(resp.text)
            if data and data.get("headline"):
                return data
        except Exception as e:
            print(f"  [!] Gemini model {m} warning/error: {e}")
            continue
    return None

def synthesize_cluster(cluster):
    sources_text = ""
    for idx, art in enumerate(cluster, 1):
        sources_text += f"\n[উৎস {idx}: {art['source']}]\nশিরোনাম: {art['title']}\nইউআরএল: {art['url']}\nবিবরণ:\n{art['body'][:1200]}\n"

    prompt = f"""
তুমি একজন দক্ষ বাংলা বার্তা সম্পাদক।
নিচের প্রতিবেদনসমূহ বিশ্লেষণ করে একটি একক, নিরপেক্ষ ও বস্তুনিষ্ঠ বাংলা সংবাদ তৈরি করো।
বিভাগ অবশ্যই এখান থেকে নির্বাচন করবে: [{CATEGORIES_LIST}]
আউটপুট ফরম্যাট (JSON):
{{
  "headline": "আকর্ষণীয় শিরোনাম",
  "category": "অনুমোদিত বিভাগ",
  "key_takeaways": ["বুলেট পয়েন্ট ১", "বুলেট পয়েন্ট ২", "বুলেট পয়েন্ট ৩"],
  "synthesized_body": "২-৩ অনুচ্ছেদে বিস্তারিত সংবাদ প্রতিবেদন।",
  "tags": ["ট্যাগ১", "ট্যাগ২"]
}}
সংবাদসমূহ:
{sources_text}
"""
    res = call_gemini(prompt)
    if res:
        res["sources"] = [{'name': a['source'], 'url': a['url']} for a in cluster]
        res["is_multi_source"] = True
        img = ""
        for a in cluster:
            if a.get('lead_image_url'):
                img = a['lead_image_url']
                break
        res["lead_image_url"] = img
    return res

def synthesize_single(art):
    prompt = f"""
তুমি একজন দক্ষ বাংলা বার্তা সম্পাদক।
নিচের একক প্রতিবেদনটি পড়ে একটি আকর্ষণীয় ও নির্ভেজাল শিরোনাম, ৩টি বুলেট পয়েন্ট এবং ২ অনুচ্ছেদে সংক্ষেপিত প্রতিবেদন তৈরি করো।
বিভাগ অবশ্যই এখান থেকে নির্বাচন করবে: [{CATEGORIES_LIST}]
আউটপুট ফরম্যাট (JSON):
{{
  "headline": "আকর্ষণীয় শিরোনাম",
  "category": "অনুমোদিত বিভাগ",
  "key_takeaways": ["বুলেট পয়েন্ট ১", "বুলেট পয়েন্ট ২", "বুলেট পয়েন্ট ৩"],
  "synthesized_body": "২ অনুচ্ছেদে সম্পূর্ণ সংবাদ।",
  "tags": ["ট্যাগ১", "ট্যাগ২"]
}}
[উৎস: {art['source']}]
শিরোনাম: {art['title']}
মূল বিবরণ: {art['body'][:1200]}
"""
    res = call_gemini(prompt)
    if res:
        res["sources"] = [{'name': art['source'], 'url': art['url']}]
        res["is_multi_source"] = False
        res["lead_image_url"] = art.get('lead_image_url', '')
    return res

def run_scan_and_stage(limit_per_source=8, max_single_items=6, auto_publish=False):
    """
    Scans all 6 portals, clusters breaking duplicates across portals,
    synthesizes all multi-source clusters, and picks diverse single-source scoops.
    If auto_publish is True: saves all synthesized articles directly as 'published'.
    If auto_publish is False: saves as 'draft' in the Staging Queue for manual review.
    """
    target_status = "published" if auto_publish else "draft"
    print(f"[*] Starting news scan (depth: {limit_per_source}/portal, auto_publish: {auto_publish}, status: {target_status})...")
    existing_urls = get_all_ingested_urls()
    existing_headlines = get_all_existing_headlines()
    
    all_articles = []
    fetchers = [
        fetch_prothom_alo,
        fetch_kaler_kantho,
        fetch_jugantor,
        fetch_tbs_bangla,
        fetch_banglanews24,
        fetch_dhaka_post
    ]
    for fetcher in fetchers:
        all_articles.extend(fetcher(limit=limit_per_source, existing_urls=existing_urls))

    raw_count = len(all_articles)
    
    # 1. Filter out already ingested URLs or duplicate titles
    fresh_articles = []
    seen_fresh_urls = set()
    for art in all_articles:
        u = art.get('url', '').strip()
        if not u or u in existing_urls or u in seen_fresh_urls:
            continue
        if is_title_duplicate(art.get('title', ''), existing_headlines=existing_headlines):
            continue
        seen_fresh_urls.add(u)
        fresh_articles.append(art)

    print(f"[*] Raw scanned: {raw_count}, Fresh unique articles: {len(fresh_articles)}")

    clusters, unclustered = cluster_articles(fresh_articles)
    print(f"[*] Multi-source clusters found: {len(clusters)}, Unclustered items: {len(unclustered)}")

    synthesized_single = []
    synthesized_clusters = []

    # 2. Synthesize single-source scoops first
    by_source = {}
    for art in unclustered:
        by_source.setdefault(art['source'], []).append(art)

    selected_unclustered = []
    while len(selected_unclustered) < max_single_items:
        added_any = False
        for src in list(by_source.keys()):
            if by_source[src] and len(selected_unclustered) < max_single_items:
                selected_unclustered.append(by_source[src].pop(0))
                added_any = True
        if not added_any:
            break

    for art in selected_unclustered:
        print(f"  [+] Synthesizing Single-Source Scoop [{art['source']}]: {art['title'][:40]}")
        res = synthesize_single(art)
        if res:
            res["status"] = target_status
            synthesized_single.append(res)

    # 3. Synthesize ALL multi-source clusters second (sorted so largest clusters are last)
    clusters.sort(key=lambda cl: len(cl))
    for cl in clusters:
        print(f"  [+] Synthesizing Multi-Source Cluster ({len(cl)} outlets: {[a['source'] for a in cl]}): {cl[0]['title'][:40]}")
        res = synthesize_cluster(cl)
        if res:
            res["status"] = target_status
            synthesized_clusters.append(res)

    # 4. Sequential save: Single scoops FIRST, combined clusters LAST
    all_to_save = synthesized_single + synthesized_clusters
    total_to_save = len(all_to_save)

    # If auto_publish is enabled, stagger published_at timestamps ascendingly
    # so multi-source clusters get the newest timestamps and reside at the top of homepage
    if auto_publish and total_to_save > 0:
        now = datetime.now(timezone.utc)
        for i, item in enumerate(all_to_save):
            pub_time = now - timedelta(seconds=(total_to_save - 1 - i))
            item["published_at"] = pub_time.strftime("%Y-%m-%d %H:%M:%S")

    processed_ids = []
    for item in all_to_save:
        aid = save_article(item, status=target_status)
        processed_ids.append(aid)

    sources_in_scan = list(set(a['source'] for a in selected_unclustered + [item for cl in clusters for item in cl]))

    return {
        "scanned_raw": raw_count,
        "fresh_unique": len(fresh_articles),
        "clusters_found": len(clusters),
        "processed_count": len(processed_ids),
        "staged_count": 0 if auto_publish else len(processed_ids),
        "published_count": len(processed_ids) if auto_publish else 0,
        "auto_publish": auto_publish,
        "target_status": target_status,
        "sources_represented": sources_in_scan
    }



if __name__ == '__main__':
    result = run_scan_and_stage(limit_per_source=6, max_single_per_source=2)
    print("Scan result:", result)


