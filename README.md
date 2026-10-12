# ☕ JuraganAI — AI Business Intelligence Assistant untuk F&B (Kafe, Resto & Kedai)

[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Python](https://img.shields.io/badge/Python-3.11%20%7C%203.14-3776AB.svg?logo=python&logoColor=white)](https://www.python.org)
[![LLM Provider](https://img.shields.io/badge/LLM-DeepSeek%20%7C%20OpenRouter-0066FF.svg)](https://openrouter.ai)
[![Telegram](https://img.shields.io/badge/Telegram%20Bot-@JuragangantengAI__bot-26A5E4.svg?logo=telegram&logoColor=white)](https://t.me/JuragangantengAI_bot)
[![Tests](https://img.shields.io/badge/Pytest-20%20Passed%20(100%25)-brightgreen.svg)]()
[![Port](https://img.shields.io/badge/Host%20Port-8005-orange.svg)]()

**JuraganAI** adalah asisten Business Intelligence cerdas yang dirancang khusus untuk pemilik usaha F&B (kafe, kedai kopi, restoran, dan UMKM kuliner) di Indonesia. Sistem ini memungkinkan pemilik usaha mengimpor data transaksi POS kasir, memantau omzet dan grafik penjualan secara visual, berkonsultasi mengenai strategi bisnis melalui AI Agent anti-halusinasi (*zero hallucination on numbers*) via Floating Widget interaktif, serta memperoleh laporan harian otomatis via Telegram setiap malam.


---

## 🌟 4 Pilar Utama MVP + Web Dashboard

| Komponen | Deskripsi & Kemampuan |
|---|---|
| **1. POS Data Importer** | Auto-detection vendor POS terpopuler di Indonesia (**Moka POS**, **Pawoon POS**, **Majoo POS**, **ESB/Olsera**, dan format Generic). Mendukung file `.csv` dan `.xlsx`, smart merging kolom Tanggal & Jam terpisah, deduplikasi nomor struk, serta validasi status transaksi (*Completed*, *Refund*, *Void*). |
| **2. Sales Analytics Engine** | Komputasi deterministik (0% halusinasi data): Total GMV, Net Sales, Total Nota, AOV (*Average Order Value*), 5 Menu Terlaris (*Best-Sellers*), Menu Sepi Peminat (*Deadstock*), Distribusi Jam Ramai (*Peak Hours*), Tren Penjualan Harian, Kontribusi Kategori Menu, Komparasi Hari Kerja vs Akhir Pekan (*Weekend Lift*), dan Distribusi *Basket Size*. |
| **3. AI Agent & Multi-Turn Memory** | Integrasi DeepSeek API (`deepseek-chat` / `deepseek-flash`) via standard function calling. Dilengkapi memori sesi percakapan multi-turn (*sliding window memory*) yang tersimpan di database, serta *Intelligent Fallback Agent Engine* yang tetap aktif saat offline/tanpa API key. |
| **4. Telegram Bot & Daily Scheduler** | Bot interaktif **[@JuragangantengAI_bot](https://t.me/JuragangantengAI_bot)** yang aktif via real-time long-polling (tanpa perlu webhook HTTPS publik). Mendukung perintah `/start`, `/omzet`, `/laporan`, `/reset`, upload file CSV kasir langsung di chat Telegram, serta otomatisasi cron harian pukul **22:30 WIB** via APScheduler. |
| **📊 Glassmorphism Dashboard** | Antarmuka web modern di **`http://localhost:8005/`** dengan 4 kartu metrik KPI, 3 visualisasi Chart.js (Area Line Chart, Doughnut Chart, Bar Chart Peak Hours), tombol uji coba preset POS 1-klik, tombol kirim laporan Telegram instan, dan live chat AI. |

---

## 🏛️ Arsitektur Sistem

```
┌────────────────────────────────────────────────────────────────────────┐
│                          ANTARMUKA PENGGUNA                            │
│   Web Dashboard (Port 8005)      │   Telegram Bot (@JuragangantengAI)  │
└──────────────────────────────────┴─────────────────────────────────────┘
                                   │
                                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│                        FASTAPI BACKEND CORE                            │
│  • POS Importer Service (Pandas/Openpyxl Auto-Detection)               │
│  • Sales Analytics Engine (Deterministik, 0% Halusinasi Finansial)     │
│  • DeepSeek AI Agent Service (Function Calling + Context Memory)       │
│  • Daily Reporter Scheduler (APScheduler Cron 22:30 WIB)               │
└────────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│                     PERSISTENSI BASIS DATA DUAL                        │
│   SQLite (Dev Lokal: juragan_ai.db)  │  PostgreSQL 16 (Docker Prod)    │
│   Tables: outlets, categories, products, transactions, chat_sessions, │
│           chat_messages, import_jobs                                   │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 🚀 Panduan Memulai Cepat (Quick Start)

### Opsi A: Menjalankan Lokal (SQLite — Cepat & Siap Pakai)

Sistem sudah dilengkapi basis data SQLite bawaan dengan 1.250 transaksi kasir sintetis siap uji:

1. **Clone repositori dan pasang dependensi:**
   ```bash
   git clone <repo-url>
   cd AI-AGENT
   pip install -r requirements.txt
   ```

2. **Konfigurasi Environment (`.env`):**
   ```env
   PORT=8005
   DATABASE_URL=sqlite+aiosqlite:///./juragan_ai.db
   LLM_PROVIDER=deepseek                     # opsi: deepseek atau openrouter
   DEEPSEEK_API_KEY=sk-your-deepseek-key
   OPENROUTER_API_KEY=sk-or-v1-your-key     # jika menggunakan openrouter
   OPENROUTER_MODEL=meta-llama/llama-3.3-70b-instruct:free
   TELEGRAM_BOT_TOKEN=your-telegram-bot-token
   TELEGRAM_DEFAULT_CHAT_ID=your-chat-id
   DAILY_REPORT_CRON_HOUR=22
   DAILY_REPORT_CRON_MINUTE=30
   ```


3. **Jalankan Aplikasi:**
   ```bash
   python run_dev.py
   ```
   *(Atau jalankan: `uvicorn app.main:app --host 0.0.0.0 --port 8005 --reload`)*

4. **Buka Browser:**
   - **Web Dashboard**: [http://localhost:8005/](http://localhost:8005/)
   - **Interactive API Docs (Swagger)**: [http://localhost:8005/docs](http://localhost:8005/docs)

---

### Opsi B: Menjalankan dengan Docker & PostgreSQL

Pastikan Docker Desktop aktif di komputer Anda:

```bash
docker compose up --build
```

Layanan yang akan berjalan:
- **Backend JuraganAI**: `http://localhost:8005`
- **PostgreSQL 16**: Port `5436` (port diisolasi agar tidak bentrok dengan instance postgres lokal lain)

---

## 📡 Ringkasan API Endpoints Utama

### 📥 1. POS Data Importer
- `POST /api/v1/pos/import` — Unggah file transaksi kasir (CSV/XLSX).
- `GET /api/v1/pos/jobs` — Riwayat log proses impor.
- `GET /api/v1/pos/sample-templates/{vendor}` — Unduh file sample template POS (`moka`, `pawoon`, `majoo`, `generic`).
- `POST /api/v1/pos/load-sample/{vendor}` — Muat otomatis sample dataset POS ke sistem (1-klik).

### 📊 2. Sales Analytics Engine
- `GET /api/v1/analytics/overview` — Ringkasan finansial (GMV, net sales, diskon, AOV, struk).
- `GET /api/v1/analytics/top-products` — 5 menu paling laris (berdasarkan kuantitas / omzet).
- `GET /api/v1/analytics/slow-moving` — Menu sepi pembeli (evaluasi stok bahan baku).
- `GET /api/v1/analytics/peak-hours` — Distribusi jam sibuk (08:00 - 23:00).
- `GET /api/v1/analytics/compare` — Komparasi performa antar-periode.
- `GET /api/v1/analytics/daily-trend` — Tren omzet harian time-series.
- `GET /api/v1/analytics/category-breakdown` — Kontribusi omzet per kategori menu.
- `GET /api/v1/analytics/day-of-week` — Analisis hari kerja vs akhir pekan (*Weekend Lift*).
- `GET /api/v1/analytics/basket-size` — Distribusi jumlah menu per struk belanja.
- `GET /api/v1/analytics/daily-report-preview` — Pratinjau teks laporan harian.
- `POST /api/v1/analytics/send-daily-report` — Kirim laporan harian ke Telegram secara manual/instan.

### 🤖 3. AI Agent Brain & Multi-Turn Memory
- `POST /api/v1/agent/chat` — Percakapan dengan JuraganAI via Tool Calling & Memory.
- `GET /api/v1/agent/conversations/{conversation_id}` — Melihat seluruh riwayat obrolan sesi.
- `DELETE /api/v1/agent/conversations/{conversation_id}` — Mereset memori sesi percakapan.

### 📱 4. Telegram Bot
- `POST /api/v1/telegram/webhook` — Webhook endpoint penerima pesan dari Telegram.
- **Long-Polling**: Aktif otomatis saat server menyala jika `TELEGRAM_BOT_TOKEN` terisi.

---

## 🧪 Pengujian Otomatis (Pytest Test Suite)

Untuk menjalankan seluruh 20 unit & integration tests:

```bash
python -m pytest tests/ -v
```

Hasil pengujian:
```text
tests/test_agent.py::test_agent_single_turn_and_session_creation PASSED       [  5%]
tests/test_agent.py::test_agent_multi_turn_conversation_and_memory PASSED     [ 10%]
tests/test_agent.py::test_multi_provider_configuration_and_fallback PASSED    [ 15%]
tests/test_analytics.py::test_parse_date_boundary PASSED                      [ 20%]
tests/test_analytics.py::test_daily_trend_endpoint PASSED                     [ 25%]
tests/test_analytics.py::test_category_breakdown_endpoint PASSED              [ 30%]
tests/test_analytics.py::test_day_of_week_endpoint PASSED                     [ 35%]
tests/test_analytics.py::test_basket_size_endpoint PASSED                     [ 40%]
tests/test_api.py::test_health_endpoint PASSED                                [ 45%]
tests/test_api.py::test_analytics_overview_endpoint PASSED                    [ 50%]
tests/test_api.py::test_agent_chat_endpoint PASSED                            [ 55%]
tests/test_api.py::test_daily_report_preview_endpoint PASSED                  [ 60%]
tests/test_importer.py::test_detect_pos_vendor PASSED                         [ 65%]
tests/test_importer.py::test_sample_template_download PASSED                  [ 70%]
tests/test_importer.py::test_load_sample_endpoint PASSED                      [ 75%]
tests/test_importer.py::test_import_jobs_list PASSED                          [ 80%]
tests/test_importer.py::test_dashboard_page PASSED                            [ 85%]
tests/test_telegram.py::test_daily_reporter_text_generation PASSED            [ 90%]
tests/test_telegram.py::test_send_daily_report_endpoint PASSED                [ 95%]
tests/test_telegram.py::test_telegram_webhook_commands PASSED                 [100%]

============================= 20 passed in 51.88s =============================
```

---

## 📚 Dokumentasi Proyek Lengkap

Seluruh catatan arsitektur dan dokumentasi per fase tersedia pada folder `docs/`:
- [PRD_v2.1.md](docs/PRD_v2.1.md) — Product Requirements Document versi 2.1.
- [ARCHITECTURE.md](docs/ARCHITECTURE.md) — Desain arsitektur, skema basis data, dan diagram alur.
- [IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md) — Master implementation plan Fase 0 hingga Fase 6.
- [API_AND_TOOLS_SPEC.md](docs/API_AND_TOOLS_SPEC.md) — Spesifikasi REST API dan JSON Schema tool function calling.
- [docs/progress/](docs/progress/) — Catatan progres berkala dari Sesi 01 hingga Sesi 09.

