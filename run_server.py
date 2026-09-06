import os
import sys
import uvicorn

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

if __name__ == "__main__":
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", 8000))
    print("=" * 60)
    print(f">> NewsPro Web Server Starting on http://{host}:{port}...")
    print(">> Homepage:     http://127.0.0.1:8000")
    print(">> Control Room: http://127.0.0.1:8000/admin")
    print("=" * 60)
    
    uvicorn.run("web.app:app", host=host, port=port, reload=False)


