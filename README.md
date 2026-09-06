# NewsPro (নিউজপ্রো)

> **স্বয়ংক্রিয় বহুমুখী বাংলা সংবাদ পোর্টাল (Autonomous Multi-Source Bengali News Platform)**

[![Live Demo](https://img.shields.io/badge/Live%20Demo-newsprolive.onrender.com-success?style=for-the-badge&logo=render)](https://newsprolive.onrender.com)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue?style=for-the-badge&logo=python)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100%2B-009688?style=for-the-badge&logo=fastapi)](https://fastapi.tiangolo.com)
[![AI Engine](https://img.shields.io/badge/AI%20Engine-Gemini%202.5%20Flash-orange?style=for-the-badge&logo=google)](https://ai.google.dev)

🌐 **Live Production Website:** [https://newsprolive.onrender.com](https://newsprolive.onrender.com)  
🎛️ **Editorial Control Room:** [https://newsprolive.onrender.com/admin/login](https://newsprolive.onrender.com/admin/login)

NewsPro is an automated digital news platform designed for the Bangladeshi news media landscape. It continuously ingests news from **6 major Bangladeshi newspapers**, intelligently groups duplicate breaking stories across competing outlets, synthesizes multi-perspective objective reports using **Gemini Flash (Free Tier)**, and publishes them with bulleted takeaways and transparent source citations.

---

## 🌟 Key Features

1. **Autonomous Multi-Outlet Ingestion:**
   * Scrapes and monitors 6 major portals without getting blocked:
     * **প্রথম আলো (Prothom Alo)** — High-speed REST API & section feeds.
     * **কালের কণ্ঠ (Kaler Kantho)** — Schema.org metadata & crawler bypass.
     * **যুগান্তর (Jugantor)** — Cloudflare-resilient DOM extraction.
     * **টিবিএস বাংলা (TBS Bangla)** — Clean section & body extraction.
     * **বাংলানিউজ২৪ (Banglanews24)** — High-speed national, politics & economy stream.
     * **ঢাকা পোস্ট (Dhaka Post)** — Rich editorial breaking news extraction.

2. **Strategy B: Hybrid Publishing Pipeline:**
   * **Multi-Source Clusters (2+ Outlets):** Fuses coverage from competing outlets into a single, comprehensive briefing. Automatically tagged with `🟢 সমন্বিত প্রতিবেদন (২টি উৎস)` and cites all original URLs.
   * **Single-Source News (Exclusive / Standalone):** Cleans up and structures single-outlet reports with 3 key takeaways, tagged with `🔵 একক উৎস (উৎস: [পত্রিকা])`.

3. **Fundamental News Information Architecture:**
   * **12 Canonical Categories:** Strict editorial topics (`বাংলাদেশ`, `রাজনীতি`, `আইন ও অপরাধ`, `বাণিজ্য`, `বিশ্ব`, `খেলা`, `বিনোদন`, `শিক্ষা`, `বিজ্ঞান ও প্রযুক্তি`, `মতামত`, `চাকরি`, `জীবনযাপন`).
   * **"সর্বশেষ" (Latest):** A high-speed, real-time chronological stream (`ORDER BY published_at DESC`) across all 12 categories, eliminating stale database category tags.

4. **Modern, Responsive Web UI:**
   * Built with **FastAPI** + **Jinja2** + **Tailwind CSS**.
   * High-readability Bengali typography via Google Font **Hind Siliguri**.
   * Clean hero lead story, 2x2 top stories grid, scrollable "সর্বশেষ সংবাদ" live sidebar, and source attribution cards.

---

## 📁 Project Structure

```
NewsPro/
├── README.md                  # Project overview & quick start
├── ARCHITECTURE.md            # In-depth engineering handbook & design manual
├── requirements.txt           # Python dependencies
├── .env                       # Environment variables (GEMINI_API_KEY)
├── .gitignore                 # Protected files (.env, DBs, temporary scripts)
│
├── database/
│   ├── newspro.db             # SQLite relational database
│   └── db.py                  # Schema definition, seed data, and query functions
│
├── web/
│   ├── app.py                 # FastAPI server & route handlers
│   └── templates/
│       ├── base.html          # Global header, nav bar, and footer
│       ├── index.html         # Homepage (Hero, top stories, latest sidebar)
│       ├── latest.html        # "সর্বশেষ" live chronological river
│       ├── category.html      # Category-specific filtered feed
│       └── article.html       # Single article page (Takeaways, body, source citations)
│
├── aggregate_6_sources.py     # End-to-end 6-outlet scraper, clusterer & synthesizer
├── batch_publish.py           # Strategy B batch publishing runner
├── test_web.py                # Automated web server test suite
└── check_pair.py              # Test utilities
```

---

## 🚀 Quick Start Guide

### 1. Prerequisites
* **Python 3.10+** (Tested on Python 3.13)
* A free Gemini API key from [Google AI Studio](https://aistudio.google.com/app/apikey).

### 2. Installation
Clone or navigate to the project directory:
```bash
cd e:\Projects\NewsPro
pip install -r requirements.txt
```

### 3. Configure API Key
Create or verify your `.env` file in the project root:
```env
GEMINI_API_KEY=your_gemini_api_key_here
```

### 4. Initialize the Database
```bash
python database/db.py
```

### 5. Run the Web Server
```bash
python -m uvicorn web.app:app --host 127.0.0.1 --port 8000 --reload
```
Open your browser and navigate to **[http://127.0.0.1:8000](http://127.0.0.1:8000)**.

---

## 🔄 Running the Aggregation & Synthesis Engine

To fetch the latest breaking stories from all 6 news portals, cluster duplicates, and publish synthesized reports into the database:

```bash
python aggregate_6_sources.py
```

To run a batch of single-source and multi-source articles:
```bash
python batch_publish.py
```

---

## 🧪 Verification & Testing

To test all routes (`/`, `/latest`, `/category/...`, `/article/...`, and REST APIs):
```bash
python test_web.py
```

---

## 📖 In-Depth Documentation

For detailed architectural decisions, anti-scraping bypass mechanisms, prompt templates, and instructions on adding new newspapers, read **[ARCHITECTURE.md](file:///e:/Projects/NewsPro/ARCHITECTURE.md)**.

