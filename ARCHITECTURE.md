# NewsPro Architecture Handbook & Engineering Manual

> **Purpose of this document:**  
> This file preserves the technical context, architecture decisions, anti-bot scraping solutions, prompt engineering schemas, and extensibility guidelines for the NewsPro codebase. Any developer or AI assistant should read this document to understand how the system works and how to extend it.

---

## 1. System Overview & The 4-Phase Pipeline

```
[6 News Portals]
       │
       ▼ (Phase 1: Ingestion & Anti-Bot Bypassing)
[Raw Cleaned Articles (JSON)]
       │
       ▼ (Phase 2: Semantic Clustering & Deduplication)
[Clusters of 2+ Outlets]  OR  [Single-Source Stories]
       │
       ▼ (Phase 3: Editorial Synthesis via Gemini Flash)
[Structured News Object (JSON)]
       │
       ▼ (Phase 4: Persistence & Presentation)
[SQLite (newspro.db)] ➔ [FastAPI App (web/app.py)] ➔ [Reader UI]
```

---

## 2. Fundamental Architectural Rules of NewsPro

### Rule A: Strict Separation of Taxonomy vs. Temporal Views
* **Taxonomy (12 Canonical Topic Categories):**  
  Every article in the database belongs to **exactly one** topic category:
  1. `বাংলাদেশ` (`bangladesh`) — জাতীয়, স্থানীয় ও সারাদেশের সংবাদ
  2. `রাজনীতি` (`politics`) — রাজনৈতিক দল, সমাবেশ, নির্বাচন, সংসদ
  3. `আইন ও অপরাধ` (`crime-justice`) — আদালত, বিচার, পুলিশি অভিযান, মামলা
  4. `বাণিজ্য` (`business`) — অর্থনীতি, ব্যাংকিং খাত, মূল্যস্ফীতি, রাজস্ব, ব্যবসা
  5. `বিশ্ব` (`world`) — আন্তর্জাতিক খবর ও কূটনীতি
  6. `খেলা` (`sports`) — ক্রিকেট, ফুটবল ও বৈশ্বিক খেলাধুলা
  7. `বিনোদন` (`entertainment`) — চলচ্চিত্র, টেলিভিশন, সংগীত, ওটিটি
  8. `শিক্ষা` (`education`) — বিশ্ববিদ্যালয়, স্কুল-কলেজ, পরীক্ষা ও ফলাফল
  9. `বিজ্ঞান ও প্রযুক্তি` (`technology`) — গ্যাজেট, কৃত্রিম বুদ্ধিমত্তা, তথ্যপ্রযুক্তি
  10. `মতামত` (`opinion`) — কলাম, সম্পাদকীয় ও বিশ্লেষণ
  11. `চাকরি` (`jobs`) — সরকারি ও বেসরকারি নিয়োগ বিজ্ঞপ্তি
  12. `জীবনযাপন` (`lifestyle`) — স্বাস্থ্য, খাদ্য, ফ্যাশন, ভ্রমণ

* **Temporal Views ("সর্বশেষ" / Latest):**  
  **"সর্বশেষ" is NOT a category in the database.** It is a query stream across all categories:
  ```sql
  SELECT a.*, c.name_bn, c.slug 
  FROM articles a 
  JOIN categories c ON a.category_id = c.id 
  ORDER BY a.published_at DESC 
  LIMIT 50;
  ```
  *Why this matters:* Tagging an article as "Latest" creates an expiration problem (having to delete the tag after 24 hours). Querying by `ORDER BY published_at DESC` ensures that the stream is always fresh and requires zero manual cleanup.

---

## 3. Scraping & Anti-Bot Engineering (The 6 Outlets)

Normal Python `requests` or `urllib` calls fail with **403 Forbidden** on Cloudflare-protected news portals because Cloudflare inspects TLS/JA3/JA4 fingerprints. We solved this using `curl_cffi` with Chrome impersonation (`impersonate="chrome120"`).

### Detailed Outlets Breakdown:

| Outlet | Discovery / URLs | Body Extraction Strategy | Bot Defense |
| :--- | :--- | :--- | :--- |
| **প্রথম আলো (Prothom Alo)** | REST API: `https://www.prothomalo.com/api/v1/stories` | Fetches article slug directly; extracts Schema.org `NewsArticle` from HTML | Open / No bot challenge |
| **কালের কণ্ঠ (Kaler Kantho)** | Homepage links with numeric IDs: `/(online\|national)/\d+` | Embedded `application/ld+json` contains exact `headline` and `articleBody` | Cloudflare Turnstile (Bypassed via `curl_cffi`) |
| **যুগান্তর (Jugantor)** | Category links: `/(national\|politics\|sports)/\d+` | H1 tag + clean `<p>` tags (excluding ads) | Cloudflare (Bypassed via `curl_cffi`) |
| **জনকণ্ঠ (Janakantha)** | Links matching `/news/\d+` | Embedded `application/ld+json` Schema.org `NewsArticle` | Standard web (200 OK) |
| **টিবিএস বাংলা (TBS Bangla)** | Links matching `/bangla/` and `news-details-\d+` | H1 tag + `<p>` tags (excluding boilerplate) | Standard web (200 OK) |
| **ঢাকা ট্রিবিউন (Dhaka Tribune)** | Links matching `/bangladesh/`, `/politics/` | H1 tag + `<p>` tags (excluding sidebar) | Standard web (200 OK) |

---

## 4. Strategy B (Hybrid Editorial Model)

NewsPro operates under **Strategy B**:
1. **Multi-Source Stories (Clusters):** When 2 or more newspapers report on the same event within 24 hours:
   * Combine the facts into a unified article.
   * If details conflict (e.g. Kaler Kantho reports 20 officers, TBS reports 21), transparently cite both.
   * Prominently display the badge: `🟢 সমন্বিত প্রতিবেদন (Xটি পত্রিকা থেকে প্রাপ্ত)`.
   * Add citations and backlinks to all reporting newspapers.
2. **Single-Source Stories (Unclustered / Exclusive):**
   * Filter out commercial fluff and ads.
   * Summarize into 3 key bullet points (Key Takeaways) with an objective 2-paragraph body.
   * Attribute prominently to the single reporting newspaper: `🔵 একক উৎস প্রতিবেদন (উৎস: [পত্রিকা])`.

---

## 5. Gemini Prompt & Schema Enforcement

We use **Gemini Flash** with `google-genai` in **JSON Mode** (`response_mime_type="application/json"`).

### Resilience / Model Fallback Cascade:
Preview models like `gemini-3.8-flash` and `gemini-3.7-flash` have a strict project limit of only **20 requests per day** on the free tier. Standard production models like `gemini-3.6-flash` and `gemini-3.5-flash-lite` have the full 1,500 requests/day and 15 RPM allowance.

NewsPro implements an automatic fallback cascade prioritizing high-quota models:
```python
candidate_models = [
    "gemini-3.6-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-flash-latest",
    "gemini-3.5-flash"
]
```
If `gemini-3.6-flash` experiences a temporary `503 UNAVAILABLE` spike, the system seamlessly cascades to `gemini-3.5-flash-lite` or `gemini-3.1-flash-lite` to ensure synthesis never fails.

### Required JSON Schema:
```json
{
  "headline": "A concise, clickbait-free, objective Bengali headline",
  "category": "Must be one of the 12 canonical Bengali category names",
  "key_takeaways": [
    "Bullet point 1: Main event/ruling/announcement",
    "Bullet point 2: Background or reasoning",
    "Bullet point 3: Next legal/political consequence"
  ],
  "synthesized_body": "2 to 3 well-written Bengali paragraphs synthesizing all facts.",
  "tags": ["Tag1", "Tag2", "Tag3"]
}
```

---

## 6. Database Schema (`database/newspro.db`)

* **`categories`:**
  * `id`, `name_bn` (e.g., 'বাণিজ্য'), `slug` (e.g., 'business'), `display_order`
* **`articles`:**
  * `id`, `slug`, `headline`, `category_id` (FK), `lead_image_url`, `key_takeaways` (JSON array), `body`, `is_lead_story`, `is_multi_source`, `source_count`, `reading_time_minutes`, `published_at`, `updated_at`
* **`article_sources`:**
  * `id`, `article_id` (FK cascade), `source_name`, `source_url`
* **`tags` & `article_tags`:**
  * Normalized many-to-many tag relations.
* **Indexes:**
  * `idx_articles_latest` on `articles(published_at DESC)` (for "সর্বশেষ" and homepage queries).
  * `idx_articles_category` on `articles(category_id, published_at DESC)` (for category feeds).
  * `idx_articles_slug` on `articles(slug)` (for sub-millisecond article lookup).

---

## 7. Editorial Control Room & Administrative Workflow

The system includes a password-protected Editorial Control Room at `/admin`.

### Key Capabilities:
1. **On-Demand News Scan (`POST /admin/scan`):**
   * Scrapes the 6 portals concurrently.
   * Compares scanned URLs against `get_all_ingested_urls()` and titles against `is_title_duplicate()` to discard duplicates.
   * Synthesizes all multi-source clusters and scoops into the database as `status = 'draft'`.
2. **Review & Staging Queue:**
   * Lists all newly synthesized stories awaiting human verification.
   * Displays source count badges, lead entity bullets, and full synthesized text.
3. **One-Click Publishing (`POST /admin/publish-all` or `POST /admin/publish/{id}`):**
   * Changes status from `'draft'` to `'published'`.
   * Newly published stories immediately appear on the live homepage and category streams.
4. **Editorial Modification (`GET /admin/edit/{id}` & `POST /admin/edit/{id}`):**
   * Editors can refine headlines, change categories, rewrite takeaways, or edit body paragraphs before publishing.
5. **Secure Authentication:**
   * Protected by HMAC-SHA256 session cookie (`admin_session`) with 7-day validity.
   * Configurable via `.env` (`ADMIN_USERNAME`, `ADMIN_PASSWORD`, `SECRET_KEY`).

---

## 8. Asynchronous Server Architecture & Gemini Rate Management

To ensure zero freezing and 100% web responsiveness:
1. **Non-Blocking Thread Offloading:**
   * Long-running crawler and LLM operations in `admin_scan_news` are wrapped with `await asyncio.to_thread(run_scan_and_stage, ...)` in `web/app.py`.
   * Uvicorn's single event loop is never blocked; public readers experience < 20ms page loads even while a heavy scan is executing.
2. **Gemini Free-Tier Compliance (15 RPM):**
   * Scan parameters are tuned (`limit_per_source=4`, `max_single_items=5`) to limit Gemini calls to 5–6 per scan.
   * Scans finish within 8–10 seconds, safely avoiding rate limit 429 errors.

---

## 9. Typography, Bengali Numerals & Header UI

1. **Bengali Numeral Legibility:**
   * The Google Font `Hind Siliguri` renders numeral `১` (1) with a nearly closed loop, causing confusion with `৭` or `,` in small timestamps.
   * Replaced primary font stack with Google's **`Noto Sans Bengali`** (`font-family: 'Noto Sans Bengali', 'Hind Siliguri', sans-serif`). In `Noto Sans Bengali`, `১` has an open, prominent circular loop that renders crisply at any size.
2. **Compact Modern Header:**
   * Merged redundant stacked bars into a single, sleek ~72px sticky header bar.
   * Features a real-time ticking Bengali date and clock with live pulsating indicator:
     `রবিবার, ৬ সেপ্টেম্বর ২০২৬ | সন্ধ্যা ৭:২৫:১০`.

---

## 10. Deep Multi-Section Crawling & Link-Level Deduplication

Rather than merely inspecting the front homepage (which only exposes 20-30 stories that stay static for hours), each scraper traverses both the homepage and its primary topic sections:

* **Prothom Alo**: Breaks beyond homepage by polling both `/api/v1/collections/latest` (breaking river) and `/api/v1/stories` (30+ fresh stories).
* **Kaler Kantho**: Traverses homepage plus `/online/national`, `/online/politics`, and `/online/business` (discovering 180+ fresh candidate stories).
* **Jugantor**: Traverses homepage plus `/national`, `/politics`, and `/economics` (50+ stories).
* **Janakantha**: Traverses homepage plus `/national`, `/politics`, and `/economics` (130+ stories).
* **TBS Bangla**: Traverses `/bangla` homepage plus `/bangla/bangladesh` and `/bangla/Economy` (80+ stories).
* **Dhaka Tribune**: Traverses homepage plus `/bangladesh`, `/politics`, and `/court` (50+ stories).

### Link-Level Pre-Filtering:
Each fetcher receives `existing_urls` and discards already-ingested URLs *before* downloading full HTML pages. This ensures:
1. Zero wasted bandwidth re-scraping existing stories.
2. Rapid execution (scanning 500+ candidate links in < 5 seconds).
3. The crawler immediately moves down into the deeper sections, guaranteeing that every scan discovers fresh news across diverse categories (politics, economy, sports, crime, technology).

| Outlet | Deep Sections Crawled | Body Extraction Strategy | Image Extraction |
| :--- | :--- | :--- | :--- |
| **প্রথম আলো (Prothom Alo)** | Breaking River API + Stories Stream | H1 + Paragraphs / Schema.org `NewsArticle` | `og:image` |
| **কালের কণ্ঠ (Kaler Kantho)** | Homepage, `/online/national`, `/online/politics`, `/online/business` | Embedded `application/ld+json` (1,400+ chars) | `og:image` |
| **যুগান্তর (Jugantor)** | Homepage, `/national`, `/politics`, `/economics` | Article container `<p>` tags (6,000+ chars) | `og:image` |
| **জনকণ্ঠ (Janakantha)** | Homepage, `/national`, `/politics`, `/economics` | Intelligent fallback to article `<p>` tags (2,100+ chars) | `og:image` |
| **টিবিএস বাংলা (TBS Bangla)** | Homepage, `/bangla/bangladesh`, `/bangla/Economy` | H1 + clean `<p>` tags (2,900+ chars) | `og:image` |
| **ঢাকা ট্রিবিউন (Dhaka Tribune)** | Homepage, `/bangladesh`, `/politics`, `/court` | H1 + article container `<p>` tags (3,300+ chars) | `og:image` |

---

## 11. Development Commands Quick Reference

| Command | Action |
| :--- | :--- |
| `python database/db.py` | Initializes SQLite schema and seeds 12 canonical categories |
| `python run_server.py` | Launches web portal & control room on `http://127.0.0.1:8000` |
| `python test_admin.py` | Runs automated test suite on authentication, staging & publishing |
| `python test_web.py` | Runs automated integration tests on all public web routes |
| `python engine/scanner.py` | Manually runs scanner and staging pipeline via CLI |
| `python scratch/probe_portals.py` | Tests live scraping and body extraction across all 6 outlets |

---

## 12. Adaptive Background Scheduler & Dual Automation Control Room

NewsPro features an autonomous background scanner engineered for real-world editorial newsrooms. It balances timely breaking news discovery with Google Gemini's free-tier rate limits and resource efficiency.

### A. Dual Control Switches (Editorial Control Room `/admin`)

Two independent toggle switches give the editor complete authority over automation:

1. **Auto-Scanner `[ON / OFF]`:**
   - **`ON`**: An asynchronous background loop runs 24/7 inside the server process. It periodically checks the clock and triggers news scans according to the Bangladesh Time window.
   - **`OFF`**: All automated background scans are suspended. Scans only occur manually when the editor clicks **"নতুন সংবাদ স্ক্যান করুন"** in the Control Room.

2. **Auto-Publish `[ON / OFF]`:**
   - **`ON`**: Every synthesized news item (both multi-source corroborated clusters and single-source scoops) is published live immediately (`status = 'published'`) to the homepage and category streams.
   - **`OFF`**: Every synthesized news item is routed to the Staging Queue (`status = 'draft'`) as an editorial draft for human review and 1-click publishing.

Both switches persist in the SQLite `system_settings` table across server restarts and container reboots.

### B. Adaptive Schedule Windows (Bangladesh Standard Time, UTC+6)

News cycles in Bangladesh peak in the morning as daily newspapers release comprehensive reports. The scheduler dynamically adapts its crawling depth and frequency:

| Time Window (BST) | Schedule Mode | Scan Frequency | Depth per Portal | Max Scoops per Scan | Focus |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **08:30 AM – 11:30 AM** | 🌅 **Morning Rush (পিক আওয়ার)** | Every **20 mins** | **12 stories** | 8 single scoops | Deep, comprehensive discovery during peak morning releases across all newspaper sections |
| **11:30 AM – 11:00 PM** | ☀️ **Standard Daytime (নিয়মিত)** | Every **30 mins** | **8 stories** | 6 single scoops | Steady daytime breaking news monitoring across 6 portals |
| **11:00 PM – 08:30 AM** | 🌙 **Overnight (রক্ষণাবেক্ষণ)** | Every **60 mins** | **5 stories** | 4 single scoops | Light, resource-conserving maintenance |

### C. Gemini Free-Tier Quota Safety:
- Maximum daily calls during peak active days remain between 350–450 requests, well within Gemini's free quota of **1,500 requests/day**.
- The fallback cascade (`gemini-3.6-flash` -> `gemini-3.5-flash-lite` -> `gemini-flash-latest`) guarantees zero 429 quota exhaustion.



