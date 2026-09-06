import sqlite3
import json
import os
import re
import sys
from datetime import datetime

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

DB_PATH = os.getenv("DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "newspro.db"))

CANONICAL_CATEGORIES = [
    ("সর্বশেষ", "latest", 0), # dynamic, not in DB
    ("বাংলাদেশ", "bangladesh", 1),
    ("রাজনীতি", "politics", 2),
    ("আইন ও অপরাধ", "crime-justice", 3),
    ("বাণিজ্য", "business", 4),
    ("বিশ্ব", "world", 5),
    ("খেলা", "sports", 6),
    ("বিনোদন", "entertainment", 7),
    ("শিক্ষা", "education", 8),
    ("বিজ্ঞান ও প্রযুক্তি", "technology", 9),
    ("মতামত", "opinion", 10),
    ("চাকরি", "jobs", 11),
    ("জীবনযাপন", "lifestyle", 12),
]

TURSO_DATABASE_URL = os.getenv("TURSO_DATABASE_URL")
TURSO_AUTH_TOKEN = os.getenv("TURSO_AUTH_TOKEN")

class LibsqlRow:
    def __init__(self, cols, values):
        self._cols = cols
        self._values = values
        self._col_map = {col: i for i, col in enumerate(cols)}

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        return self._values[self._col_map[key]]

    def get(self, key, default=None):
        idx = self._col_map.get(key)
        return self._values[idx] if idx is not None else default

    def keys(self):
        return self._cols

    def __iter__(self):
        return iter(self._cols)

    def __repr__(self):
        return dict(self).__repr__()

    def items(self):
        return [(c, self._values[i]) for i, c in enumerate(self._cols)]

class LibsqlCursorWrapper:
    def __init__(self, raw_cursor):
        self._cur = raw_cursor

    def execute(self, sql, params=()):
        self._cur.execute(sql, params)
        return self

    def executemany(self, sql, seq_of_params):
        self._cur.executemany(sql, seq_of_params)
        return self

    def executescript(self, script):
        self._cur.executescript(script)
        return self

    def fetchone(self):
        val = self._cur.fetchone()
        if val is None:
            return None
        cols = [d[0] for d in self._cur.description]
        return LibsqlRow(cols, val)

    def fetchall(self):
        vals = self._cur.fetchall()
        if not vals:
            return []
        cols = [d[0] for d in self._cur.description]
        return [LibsqlRow(cols, v) for v in vals]

    @property
    def lastrowid(self):
        return self._cur.lastrowid

    @property
    def rowcount(self):
        return self._cur.rowcount

    def close(self):
        self._cur.close()

class LibsqlConnectionWrapper:
    def __init__(self, raw_conn):
        self._conn = raw_conn

    def cursor(self):
        return LibsqlCursorWrapper(self._conn.cursor())

    def execute(self, sql, params=()):
        cur = self.cursor()
        cur.execute(sql, params)
        return cur

    def executescript(self, script):
        cur = self.cursor()
        cur.executescript(script)
        return cur

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()

def get_db():
    turso_url = os.getenv("TURSO_DATABASE_URL")
    turso_token = os.getenv("TURSO_AUTH_TOKEN")
    if turso_url and turso_token:
        try:
            import libsql
            raw_conn = libsql.connect(turso_url, auth_token=turso_token)
            return LibsqlConnectionWrapper(raw_conn)
        except Exception as e:
            print(f"[!] Failed to connect to Turso ({e}), falling back to local SQLite.")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def make_slug(headline, article_id=None):
    clean = re.sub(r'[^\w\s\u0980-\u09FF-]', '', headline)
    clean = re.sub(r'[\s_]+', '-', clean).strip('-')
    if len(clean) > 80:
        clean = clean[:80].rsplit('-', 1)[0]
    suffix = str(article_id or int(datetime.now().timestamp()))
    return f"{clean}-{suffix[-6:]}"

def init_db():
    conn = get_db()
    cursor = conn.cursor()

    cursor.executescript("""
    CREATE TABLE IF NOT EXISTS categories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name_bn TEXT NOT NULL UNIQUE,
        slug TEXT NOT NULL UNIQUE,
        display_order INTEGER DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS articles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        slug TEXT NOT NULL UNIQUE,
        headline TEXT NOT NULL,
        category_id INTEGER NOT NULL REFERENCES categories(id),
        lead_image_url TEXT,
        key_takeaways TEXT NOT NULL, -- JSON array
        body TEXT NOT NULL,
        status TEXT DEFAULT 'published', -- 'draft' (staged) or 'published'
        is_lead_story BOOLEAN DEFAULT FALSE,
        is_multi_source BOOLEAN DEFAULT FALSE,
        source_count INTEGER DEFAULT 1,
        reading_time_minutes INTEGER DEFAULT 2,
        published_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS article_sources (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        article_id INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
        source_name TEXT NOT NULL,
        source_url TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS tags (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE
    );

    CREATE TABLE IF NOT EXISTS article_tags (
        article_id INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
        tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
        PRIMARY KEY (article_id, tag_id)
    );

    CREATE TABLE IF NOT EXISTS system_settings (
        key TEXT PRIMARY KEY,
        value TEXT
    );

    CREATE INDEX IF NOT EXISTS idx_articles_latest ON articles(published_at DESC);
    CREATE INDEX IF NOT EXISTS idx_articles_category ON articles(category_id, published_at DESC);
    CREATE INDEX IF NOT EXISTS idx_articles_slug ON articles(slug);
    """)

    # Ensure status column exists if migrated from earlier schema
    cursor.execute("PRAGMA table_info(articles)")
    columns = [row["name"] for row in cursor.fetchall()]
    if "status" not in columns:
        cursor.execute("ALTER TABLE articles ADD COLUMN status TEXT DEFAULT 'published'")

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_articles_status ON articles(status, published_at DESC)")

    # Seed categories (skipping 'latest' which is a virtual view)
    for name_bn, slug, order in CANONICAL_CATEGORIES:
        if slug == "latest":
            continue
        cursor.execute(
            "INSERT OR IGNORE INTO categories (name_bn, slug, display_order) VALUES (?, ?, ?)",
            (name_bn, slug, order)
        )

    # Seed default system settings
    cursor.execute("INSERT OR IGNORE INTO system_settings (key, value) VALUES ('auto_scanner_enabled', 'true')")
    cursor.execute("INSERT OR IGNORE INTO system_settings (key, value) VALUES ('auto_publish_enabled', 'true')")

    # If articles table is empty, auto-seed from seed_articles.json
    cursor.execute("SELECT COUNT(*) as cnt FROM articles")
    if cursor.fetchone()["cnt"] == 0:
        seed_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seed_articles.json")
        if os.path.exists(seed_path):
            try:
                with open(seed_path, "r", encoding="utf-8") as f:
                    seed_articles = json.load(f)

                cat_rows = cursor.execute("SELECT id, name_bn FROM categories").fetchall()
                cat_map = {r["name_bn"]: r["id"] for r in cat_rows}
                default_cat_id = cat_map.get("বাংলাদেশ", 1)

                for idx, item in enumerate(seed_articles):
                    headline = item["headline"].strip()
                    slug = make_slug(headline, idx + 1)
                    cat_id = cat_map.get(item.get("category"), default_cat_id)
                    takeaways_json = json.dumps(item.get("key_takeaways", []), ensure_ascii=False)
                    body = item.get("synthesized_body", "").strip()
                    words = len(body.split())
                    reading_time = max(1, round(words / 180))
                    sources = item.get("sources", [])
                    is_multi = item.get("is_multi_source", len(sources) > 1)
                    status = item.get("status", "published")
                    pub_at = item.get("published_at")

                    if pub_at:
                        cursor.execute("""
                            INSERT INTO articles (
                                slug, headline, category_id, lead_image_url, key_takeaways,
                                body, status, is_multi_source, source_count, reading_time_minutes, published_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (slug, headline, cat_id, item.get("lead_image_url", ""),
                              takeaways_json, body, status, is_multi, len(sources), reading_time, pub_at))
                    else:
                        cursor.execute("""
                            INSERT INTO articles (
                                slug, headline, category_id, lead_image_url, key_takeaways,
                                body, status, is_multi_source, source_count, reading_time_minutes
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (slug, headline, cat_id, item.get("lead_image_url", ""),
                              takeaways_json, body, status, is_multi, len(sources), reading_time))

                    art_id = cursor.lastrowid
                    for s in sources:
                        cursor.execute("""
                            INSERT INTO article_sources (article_id, source_name, source_url)
                            VALUES (?, ?, ?)
                        """, (art_id, s.get("name", "উৎস"), s.get("url", "#")))

                    for tag in item.get("tags", []):
                        tag_clean = tag.strip()
                        if not tag_clean:
                            continue
                        cursor.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (tag_clean,))
                        cursor.execute("SELECT id FROM tags WHERE name = ?", (tag_clean,))
                        tag_id = cursor.fetchone()["id"]
                        cursor.execute("INSERT OR IGNORE INTO article_tags (article_id, tag_id) VALUES (?, ?)", (art_id, tag_id))

                print(f"[✓] Auto-seeded {len(seed_articles)} articles from seed_articles.json.")
            except Exception as e:
                print(f"[!] Error auto-seeding articles: {e}")

    conn.commit()
    conn.close()
    print("[✓] Database initialized and canonical categories seeded.")

def save_article(data, status="draft"):
    """
    Saves a synthesized or curated article into the database.
    Default status is 'draft' (staged for editorial review).
    """
    conn = get_db()
    cursor = conn.cursor()

    cat_name = data.get("category", "বাংলাদেশ")
    cursor.execute("SELECT id FROM categories WHERE name_bn = ?", (cat_name,))
    row = cursor.fetchone()
    if not row:
        cursor.execute("SELECT id FROM categories WHERE slug = 'bangladesh'")
        row = cursor.fetchone()
    category_id = row["id"]

    headline = data["headline"].strip()
    cursor.execute("SELECT id FROM articles WHERE headline = ?", (headline,))
    existing = cursor.fetchone()
    if existing:
        conn.close()
        return existing["id"]

    slug = make_slug(headline)
    body = data.get("synthesized_body", "").strip()
    words = len(body.split())
    reading_time = max(1, round(words / 180))

    takeaways_json = json.dumps(data.get("key_takeaways", []), ensure_ascii=False)
    sources = data.get("sources", [])
    is_multi = len(sources) > 1 or data.get("is_multi_source", False)
    final_status = data.get("status", status)
    published_at_val = data.get("published_at")

    if published_at_val:
        cursor.execute("""
            INSERT INTO articles (
                slug, headline, category_id, lead_image_url, key_takeaways,
                body, status, is_multi_source, source_count, reading_time_minutes, published_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            slug, headline, category_id, data.get("lead_image_url", ""),
            takeaways_json, body, final_status, is_multi, len(sources), reading_time, published_at_val
        ))
    else:
        cursor.execute("""
            INSERT INTO articles (
                slug, headline, category_id, lead_image_url, key_takeaways,
                body, status, is_multi_source, source_count, reading_time_minutes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            slug, headline, category_id, data.get("lead_image_url", ""),
            takeaways_json, body, final_status, is_multi, len(sources), reading_time
        ))
    article_id = cursor.lastrowid

    for s in sources:
        cursor.execute("""
            INSERT INTO article_sources (article_id, source_name, source_url)
            VALUES (?, ?, ?)
        """, (article_id, s.get("name", "অজ্ঞাত"), s.get("url", "#")))

    for tag in data.get("tags", []):
        tag_clean = tag.strip()
        if not tag_clean: continue
        cursor.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (tag_clean,))
        cursor.execute("SELECT id FROM tags WHERE name = ?", (tag_clean,))
        tag_id = cursor.fetchone()["id"]
        cursor.execute("INSERT OR IGNORE INTO article_tags (article_id, tag_id) VALUES (?, ?)", (article_id, tag_id))

    conn.commit()
    conn.close()
    return article_id

def get_categories():
    conn = get_db()
    rows = conn.execute("SELECT * FROM categories ORDER BY display_order ASC").fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_latest_articles(limit=30, offset=0, status="published"):
    conn = get_db()
    query = """
        SELECT a.*, c.name_bn as category_name, c.slug as category_slug
        FROM articles a
        JOIN categories c ON a.category_id = c.id
        WHERE a.status = ?
        ORDER BY a.published_at DESC, a.is_multi_source DESC, a.source_count DESC, a.id DESC
        LIMIT ? OFFSET ?
    """
    rows = conn.execute(query, (status, limit, offset)).fetchall()
    articles = []
    for r in rows:
        d = dict(r)
        d["key_takeaways"] = json.loads(d["key_takeaways"]) if d["key_takeaways"] else []
        articles.append(d)
    conn.close()
    return articles

def get_category_articles(category_slug, limit=20, offset=0):
    conn = get_db()
    query = """
        SELECT a.*, c.name_bn as category_name, c.slug as category_slug
        FROM articles a
        JOIN categories c ON a.category_id = c.id
        WHERE c.slug = ? AND a.status = 'published'
        ORDER BY a.published_at DESC, a.is_multi_source DESC, a.source_count DESC, a.id DESC
        LIMIT ? OFFSET ?
    """
    rows = conn.execute(query, (category_slug, limit, offset)).fetchall()
    articles = []
    for r in rows:
        d = dict(r)
        d["key_takeaways"] = json.loads(d["key_takeaways"]) if d["key_takeaways"] else []
        articles.append(d)
    conn.close()
    return articles

def get_article_by_slug(slug):
    conn = get_db()
    query = """
        SELECT a.*, c.name_bn as category_name, c.slug as category_slug
        FROM articles a
        JOIN categories c ON a.category_id = c.id
        WHERE a.slug = ?
    """
    row = conn.execute(query, (slug,)).fetchone()
    if not row:
        conn.close()
        return None
    d = dict(row)
    d["key_takeaways"] = json.loads(d["key_takeaways"]) if d["key_takeaways"] else []
    
    sources = conn.execute("SELECT source_name, source_url FROM article_sources WHERE article_id = ?", (d["id"],)).fetchall()
    d["sources"] = [dict(s) for s in sources]
    
    tags = conn.execute("""
        SELECT t.name FROM tags t
        JOIN article_tags at ON t.id = at.tag_id
        WHERE at.article_id = ?
    """, (d["id"],)).fetchall()
    d["tags"] = [t["name"] for t in tags]

    conn.close()
    return d

def get_article_by_id(article_id):
    conn = get_db()
    query = """
        SELECT a.*, c.name_bn as category_name, c.slug as category_slug
        FROM articles a
        JOIN categories c ON a.category_id = c.id
        WHERE a.id = ?
    """
    row = conn.execute(query, (article_id,)).fetchone()
    if not row:
        conn.close()
        return None
    d = dict(row)
    d["key_takeaways"] = json.loads(d["key_takeaways"]) if d["key_takeaways"] else []
    
    sources = conn.execute("SELECT source_name, source_url FROM article_sources WHERE article_id = ?", (d["id"],)).fetchall()
    d["sources"] = [dict(s) for s in sources]
    
    tags = conn.execute("""
        SELECT t.name FROM tags t
        JOIN article_tags at ON t.id = at.tag_id
        WHERE at.article_id = ?
    """, (d["id"],)).fetchall()
    d["tags"] = [t["name"] for t in tags]

    conn.close()
    return d

# ==========================================
# ADMIN & STAGING OPERATIONS
# ==========================================

def get_staged_articles():
    """Returns all articles in draft status awaiting editorial review."""
    conn = get_db()
    query = """
        SELECT a.*, c.name_bn as category_name, c.slug as category_slug
        FROM articles a
        JOIN categories c ON a.category_id = c.id
        WHERE a.status = 'draft'
        ORDER BY a.is_multi_source DESC, a.source_count DESC, a.published_at DESC, a.id DESC
    """
    rows = conn.execute(query).fetchall()
    articles = []
    for r in rows:
        d = dict(r)
        d["key_takeaways"] = json.loads(d["key_takeaways"]) if d["key_takeaways"] else []
        sources = conn.execute("SELECT source_name, source_url FROM article_sources WHERE article_id = ?", (d["id"],)).fetchall()
        d["sources"] = [dict(s) for s in sources]
        articles.append(d)
    conn.close()
    return articles

def get_published_articles(limit=50, offset=0):
    """Returns all published articles for admin management."""
    return get_latest_articles(limit=limit, offset=offset, status="published")

def publish_article(article_id):
    """Publishes a single draft article, pushing it live."""
    conn = get_db()
    conn.execute("""
        UPDATE articles 
        SET status = 'published', published_at = CURRENT_TIMESTAMP 
        WHERE id = ?
    """, (article_id,))
    conn.commit()
    conn.close()
    return True

def publish_all_staged():
    """
    Publishes all staged draft articles in one click.
    Ensures single-source scoops are approved first and combined / multi-source
    articles are approved LAST with the freshest timestamps, so combined news
    naturally resides at the top of the homepage.
    """
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, is_multi_source, source_count 
        FROM articles 
        WHERE status = 'draft'
        ORDER BY is_multi_source ASC, source_count ASC, id ASC
    """)
    drafts = cursor.fetchall()
    if not drafts:
        conn.close()
        return 0

    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    total = len(drafts)

    for i, d in enumerate(drafts):
        # Stagger timestamps ascendingly:
        # Earlier items (single-source scoops) get earlier seconds
        # The final items (combined multi-source clusters) get the latest timestamps
        pub_time = now - timedelta(seconds=(total - 1 - i))
        pub_time_str = pub_time.strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute("""
            UPDATE articles 
            SET status = 'published', published_at = ? 
            WHERE id = ?
        """, (pub_time_str, d["id"]))

    conn.commit()
    conn.close()
    return total

def delete_article(article_id):
    """Permanently deletes an article."""
    conn = get_db()
    conn.execute("DELETE FROM articles WHERE id = ?", (article_id,))
    conn.commit()
    conn.close()
    return True

def update_article(article_id, data):
    """Updates an article's headline, category, body, takeaways."""
    conn = get_db()
    category_id = data.get("category_id")
    if not category_id and "category" in data:
        row = conn.execute("SELECT id FROM categories WHERE name_bn = ?", (data["category"],)).fetchone()
        if row: category_id = row["id"]
    
    takeaways_json = json.dumps(data.get("key_takeaways", []), ensure_ascii=False) if isinstance(data.get("key_takeaways"), list) else data.get("key_takeaways", "[]")

    conn.execute("""
        UPDATE articles 
        SET headline = ?, category_id = ?, key_takeaways = ?, body = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (data["headline"], category_id, takeaways_json, data["body"], article_id))
    conn.commit()
    conn.close()
    return True

def get_admin_stats():
    """Returns counts for the admin dashboard header."""
    conn = get_db()
    staged = conn.execute("SELECT COUNT(*) as c FROM articles WHERE status = 'draft'").fetchone()["c"]
    published = conn.execute("SELECT COUNT(*) as c FROM articles WHERE status = 'published'").fetchone()["c"]
    conn.close()
    return {
        "staged_count": staged,
        "published_count": published,
        "total_sources": 6
    }

def get_home_data():
    articles = get_latest_articles(limit=30, status="published")
    categories = get_categories()
    
    lead_story = articles[0] if articles else None
    top_stories = articles[1:5] if len(articles) > 1 else []
    latest_sidebar = articles[:10]

    category_sections = []
    for cat in categories:
        cat_articles = get_category_articles(cat["slug"], limit=4)
        if cat_articles:
            category_sections.append({
                "category": cat,
                "articles": cat_articles
            })

    return {
        "lead_story": lead_story,
        "top_stories": top_stories,
        "latest_sidebar": latest_sidebar,
        "categories": categories,
        "category_sections": category_sections
    }


def get_all_ingested_urls():

    """Returns a set of all source URLs already processed into the database (both draft and published)."""
    conn = get_db()
    rows = conn.execute("SELECT source_url FROM article_sources").fetchall()
    conn.close()
    urls = set()
    for r in rows:
        u = r["source_url"].strip()
        if u and u != "#":
            urls.add(u)
            urls.add(u.rstrip("/"))
            urls.add(u.rstrip("/") + "/")
    return urls

def is_title_duplicate(headline, threshold=0.55):
    """
    Checks if a headline strongly overlaps with any existing article in the database.
    Uses token-level Jaccard similarity.
    """
    stopwords = set([
        'ও', 'এবং', 'থেকে', 'করা', 'হয়েছে', 'হল', 'হলো', 'করে', 'বা', 'না', 
        'নিয়ে', 'যা', 'হচ্ছে', 'এই', 'এক', 'দুই', 'তিন', 'চার', 'পাঁচ', 'ছয়', 
        'লাখ', 'কোটি', 'টাকা', 'পর', 'কী', 'কে', 'দিয়ে', 'জন্য', 'বড়', 'হতে', 'পেলেন'
    ])
    words = re.findall(r'[\u0980-\u09FF]+', headline)
    tokens_query = set(w for w in words if len(w) > 2 and w not in stopwords)
    if not tokens_query:
        return False

    conn = get_db()
    rows = conn.execute("SELECT headline FROM articles").fetchall()
    conn.close()

    for r in rows:
        existing_words = re.findall(r'[\u0980-\u09FF]+', r["headline"])
        tokens_existing = set(w for w in existing_words if len(w) > 2 and w not in stopwords)
        overlap = tokens_query.intersection(tokens_existing)
        union = tokens_query.union(tokens_existing)
        jaccard = len(overlap) / len(union) if union else 0
        if (len(overlap) >= 3 and jaccard >= threshold) or len(overlap) >= 4:
            return True
    return False

def get_system_setting(key, default=""):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE IF NOT EXISTS system_settings (key TEXT PRIMARY KEY, value TEXT)")
    row = cursor.execute("SELECT value FROM system_settings WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default

def set_system_setting(key, value):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE IF NOT EXISTS system_settings (key TEXT PRIMARY KEY, value TEXT)")
    cursor.execute("INSERT INTO system_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, str(value)))
    conn.commit()
    conn.close()

if __name__ == "__main__":
    init_db()

