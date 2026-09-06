import os
import sys
import hmac
import hashlib
import time
import asyncio
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

# Add parent directory to path so database & engine imports work
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

load_dotenv()

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "newspro2026")
SECRET_KEY = os.getenv("SECRET_KEY", "newspro-super-secret-key-bangladesh-2026")

def create_session_token(username: str) -> str:
    ts = str(int(time.time()))
    payload = f"{username}:{ts}"
    sig = hmac.new(SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}:{sig}"

def verify_session_token(token: str) -> bool:
    if not token or ":" not in token:
        return False
    parts = token.split(":")
    if len(parts) != 3:
        return False
    username, ts_str, sig = parts
    try:
        ts = int(ts_str)
    except ValueError:
        return False
    # Valid for 7 days
    if time.time() - ts > 7 * 86400 or time.time() < ts - 60:
        return False
    payload = f"{username}:{ts_str}"
    expected_sig = hmac.new(SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected_sig):
        return False
    return username == ADMIN_USERNAME

def is_authenticated(request: Request) -> bool:
    token = request.cookies.get("admin_session")
    return verify_session_token(token)

from database.db import (
    init_db,
    get_categories,
    get_latest_articles,
    get_category_articles,
    get_article_by_slug,
    get_article_by_id,
    get_home_data,
    get_staged_articles,
    get_published_articles,
    publish_article,
    publish_all_staged,
    delete_article,
    update_article,
    get_admin_stats
)
from web.time_utils import timeago_bn, format_datetime_bn, to_bn_digits
from engine.scanner import run_scan_and_stage
from engine.scheduler import scheduler

app = FastAPI(title="NewsPro API & Web Portal")

@app.on_event("startup")
async def startup_scheduler():
    init_db()
    asyncio.create_task(scheduler.run_loop())

@app.middleware("http")
async def admin_auth_middleware(request: Request, call_next):
    path = request.url.path
    # Protect any /admin route EXCEPT /admin/login
    if path.startswith("/admin") and path not in ["/admin/login"]:
        if not is_authenticated(request):
            return RedirectResponse(url="/admin/login?error=অনুগ্রহ করে প্রথমে লগইন করুন", status_code=303)
    response = await call_next(request)
    return response

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
templates = Jinja2Templates(directory=TEMPLATES_DIR)

# Register Jinja2 template filters for Bengali datetime formatting
templates.env.filters["timeago_bn"] = timeago_bn
templates.env.filters["format_datetime_bn"] = format_datetime_bn
templates.env.filters["to_bn_digits"] = to_bn_digits


# ==========================================
# PUBLIC ROUTES
# ==========================================

@app.get("/", response_class=HTMLResponse)
async def home_page(request: Request):
    data = get_home_data()
    return templates.TemplateResponse(request=request, name="index.html", context={
        "categories": data["categories"],
        "lead_story": data["lead_story"],
        "top_stories": data["top_stories"],
        "latest_sidebar": data["latest_sidebar"],
        "category_sections": data.get("category_sections", []),
        "active_page": "home",
        "active_category": None
    })


@app.get("/latest", response_class=HTMLResponse)
async def latest_page(request: Request):
    articles = get_latest_articles(limit=50, status="published")
    categories = get_categories()
    return templates.TemplateResponse(request=request, name="latest.html", context={
        "articles": articles,
        "categories": categories,
        "active_page": "latest",
        "active_category": None
    })

@app.get("/category/{category_slug}", response_class=HTMLResponse)
async def category_page(request: Request, category_slug: str):
    categories = get_categories()
    matched_cat = next((c for c in categories if c["slug"] == category_slug), None)
    if not matched_cat:
        raise HTTPException(status_code=404, detail="Category not found")

    articles = get_category_articles(category_slug, limit=30)
    return templates.TemplateResponse(request=request, name="category.html", context={
        "category_name": matched_cat["name_bn"],
        "category_slug": category_slug,
        "articles": articles,
        "categories": categories,
        "active_page": "category",
        "active_category": category_slug
    })

@app.get("/article/{slug}", response_class=HTMLResponse)
async def article_page(request: Request, slug: str):
    article = get_article_by_slug(slug)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    
    categories = get_categories()
    return templates.TemplateResponse(request=request, name="article.html", context={
        "article": article,
        "categories": categories,
        "active_page": "article",
        "active_category": article["category_slug"]
    })

# ==========================================
# EDITORIAL CONTROL ROOM (ADMIN ROUTES)
# ==========================================

@app.get("/admin/login", response_class=HTMLResponse)
async def admin_login_page(request: Request, error: str = None, msg: str = None):
    """Displays the admin login page or redirects if already authenticated."""
    if is_authenticated(request):
        return RedirectResponse(url="/admin", status_code=303)
    categories = get_categories()
    return templates.TemplateResponse(request=request, name="admin_login.html", context={
        "error": error,
        "msg": msg,
        "categories": categories,
        "active_page": "admin",
        "active_category": None
    })

@app.post("/admin/login")
async def admin_login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...)
):
    """Authenticates admin credentials and sets session cookie."""
    if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
        token = create_session_token(username)
        response = RedirectResponse(url="/admin", status_code=303)
        response.set_cookie(
            key="admin_session",
            value=token,
            httponly=True,
            max_age=86400 * 7,
            samesite="lax"
        )
        return response
    else:
        categories = get_categories()
        return templates.TemplateResponse(
            request=request,
            name="admin_login.html",
            context={
                "error": "ব্যবহারকারী নাম অথবা পাসওয়ার্ড সঠিক নয়। অনুগ্রহ করে আবার চেষ্টা করুন।",
                "categories": categories,
                "active_page": "admin",
                "active_category": None
            },
            status_code=400
        )

@app.get("/admin/logout")
async def admin_logout():
    """Logs out admin user by clearing session cookie."""
    response = RedirectResponse(url="/admin/login?msg=আপনি সফলভাবে লগআউট করেছেন।", status_code=303)
    response.delete_cookie(key="admin_session")
    return response

@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(request: Request, msg: str = None):

    staged = get_staged_articles()
    stats = get_admin_stats()
    published = get_published_articles(limit=max(100, stats["published_count"]))
    categories = get_categories()
    scheduler_status = scheduler.get_status()

    return templates.TemplateResponse(request=request, name="admin.html", context={
        "staged_articles": staged,
        "published_articles": published,
        "stats": stats,
        "categories": categories,
        "scheduler_status": scheduler_status,
        "msg": msg,
        "active_page": "admin",
        "active_category": None
    })

@app.post("/admin/scan")
async def admin_scan_news():
    """Scans the 6 portals, clusters breaking duplicates across portals, and applies auto_publish setting."""
    try:
        auto_publish = scheduler.is_auto_publish_enabled()
        window = scheduler.get_status()["window"]
        # On manual scan, run with deep limits (at least 10 per source, up to 8 single scoops + all clusters)
        limit_per_source = max(10, window.get("limit_per_source", 10))
        max_single_items = max(8, window.get("max_single_items", 8))
        summary = await asyncio.to_thread(
            run_scan_and_stage,
            limit_per_source=limit_per_source,
            max_single_items=max_single_items,
            auto_publish=auto_publish
        )
        multi_text = f"{summary['clusters_found']}টি সমন্বিত বহুমাত্রিক সংবাদ (Multi-Source)" if summary['clusters_found'] else "কোনো বহু-উৎস ক্লাস্টার মেলেনি"
        if auto_publish:
            action_text = f"মোট {summary['published_count']}টি নতুন সংবাদ সরাসরি লাইভ প্রকাশিত হয়েছে ({multi_text})।"
        else:
            action_text = f"মোট {summary['staged_count']}টি ড্রাফট খসড়ায় যুক্ত হয়েছে ({multi_text})।"
        msg = f"স্ক্যান সম্পন্ন! {summary['scanned_raw']}টি সংবাদের মধ্যে {summary['fresh_unique']}টি নতুন সংবাদ বিশ্লেষণ করা হয়েছে। {action_text}"
    except Exception as e:
        msg = f"স্ক্যান ত্রুটি: {e}"
    return RedirectResponse(url=f"/admin?msg={msg}", status_code=303)

@app.post("/admin/settings/toggle-scanner")
async def toggle_scanner():
    current = scheduler.is_auto_scanner_enabled()
    scheduler.set_auto_scanner_enabled(not current)
    state_str = "চালু (ON)" if not current else "বন্ধ (OFF)"
    return RedirectResponse(url=f"/admin?msg=স্বয়ংক্রিয় স্ক্যানার সফলভাবে {state_str} করা হয়েছে।", status_code=303)

@app.post("/admin/settings/toggle-publish")
async def toggle_publish():
    current = scheduler.is_auto_publish_enabled()
    scheduler.set_auto_publish_enabled(not current)
    state_str = "সরাসরি লাইভ প্রকাশনা (ON)" if not current else "ড্রাফট খসড়া কিউ (OFF)"
    return RedirectResponse(url=f"/admin?msg=স্বয়ংক্রিয় প্রকাশনা মোড: {state_str} করা হয়েছে।", status_code=303)


@app.post("/admin/publish/{article_id}")
async def admin_publish_article(article_id: int):
    """Publishes an individual staged draft story."""
    publish_article(article_id)
    return RedirectResponse(url="/admin?msg=সংবাদটি সফলভাবে প্রকাশ করা হয়েছে!", status_code=303)

@app.post("/admin/publish-all")
async def admin_publish_all():
    """Publishes all staged draft stories in one click."""
    count = publish_all_staged()
    return RedirectResponse(url=f"/admin?msg={count}টি অপেক্ষমাণ ড্রাফট এক ক্লিকে প্রকাশ করা হয়েছে!", status_code=303)

@app.post("/admin/delete/{article_id}")
async def admin_delete_article(article_id: int):
    """Deletes an article (draft or published)."""
    delete_article(article_id)
    return RedirectResponse(url="/admin?msg=সংবাদটি মুছে ফেলা হয়েছে।", status_code=303)

@app.get("/admin/edit/{article_id}", response_class=HTMLResponse)
async def admin_edit_page(request: Request, article_id: int):
    article = get_article_by_id(article_id)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    
    categories = get_categories()
    return templates.TemplateResponse(request=request, name="admin_edit.html", context={
        "article": article,
        "categories": categories,
        "active_page": "admin",
        "active_category": None
    })

@app.post("/admin/edit/{article_id}")
async def admin_edit_save(
    article_id: int,
    headline: str = Form(...),
    category_id: int = Form(...),
    takeaways_text: str = Form(""),
    body: str = Form(...),
    action: str = Form("save")
):
    # Parse bullet takeaways per line
    takeaways = [line.strip() for line in takeaways_text.splitlines() if line.strip()]
    
    update_article(article_id, {
        "headline": headline,
        "category_id": category_id,
        "key_takeaways": takeaways,
        "body": body
    })

    if action == "save_and_publish":
        publish_article(article_id)
        msg = "সংবাদটি সফলভাবে সম্পাদিত ও প্রকাশিত হয়েছে!"
    else:
        msg = "সংবাদের পরিবর্তন সফলভাবে সংরক্ষিত হয়েছে।"

    return RedirectResponse(url=f"/admin?msg={msg}", status_code=303)

# ==========================================
# REST API ENDPOINTS
# ==========================================

@app.get("/api/articles/latest")
async def api_latest(limit: int = 20):
    return {"articles": get_latest_articles(limit=limit, status="published")}

@app.get("/api/categories")
async def api_categories():
    return {"categories": get_categories()}

@app.get("/api/db-status")
async def db_status():
    turso_url = os.getenv("TURSO_DATABASE_URL")
    turso_token = os.getenv("TURSO_AUTH_TOKEN")
    err = None
    connected = False
    article_count = 0
    backend = "sqlite_local"
    try:
        from database.db import get_db
        conn = get_db()
        if hasattr(conn, "_raw"):
            backend = "turso_cloud"
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) as c FROM articles")
        article_count = cur.fetchone()["c"]
        connected = True
        conn.close()
    except Exception as e:
        err = str(e)
    return {
        "has_turso_url": bool(turso_url),
        "url_prefix": turso_url[:15] if turso_url else None,
        "has_turso_token": bool(turso_token),
        "backend": backend,
        "connected": connected,
        "article_count": article_count,
        "error": err
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("web.app:app", host="127.0.0.1", port=8000, reload=True)
