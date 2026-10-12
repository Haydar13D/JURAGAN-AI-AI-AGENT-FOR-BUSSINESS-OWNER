# Master Implementation Plan
## JuraganAI — AI Business Intelligence Assistant MVP

---

## 1. Ikhtisar Rencana Implementasi (Implementation Overview)
Rencana implementasi ini dirancang secara modular dan berurutan untuk membangun sistem **JuraganAI MVP** mulai dari pondasi backend, generator dataset sintetis, analitik deterministik, integrasi AI Agent dengan DeepSeek Tool Calling, hingga bot Telegram dengan scheduler laporan harian.

---

## 2. Fase Pengembangan (Development Phases)

```
[ Fase 0: Setup & Pondasi ]
           │
           ▼
[ Fase 1: Synthetic Dataset & POS Importer ]
           │
           ▼
[ Fase 2: Sales Analytics Engine ]
           │
           ▼
[ Fase 3: AI Agent & DeepSeek Tool Calling ]
           │
           ▼
[ Fase 4: Telegram Bot & Daily Reporting Scheduler ]
           │
           ▼
[ Fase 5: Dockerization, Testing & Dokumentasi Akhir ]
```

---

### Fase 0: Inisialisasi Proyek, Konfigurasi & Basis Data
- **Tujuan**: Membangun struktur proyek FastAPI yang bersih, konfigurasi environment, dan koneksi PostgreSQL via async SQLAlchemy.
- **Tugas Spesifik**:
  1. Buat struktur direktori modular:
     ```
     f:/AI-AGENT/
     ├── app/
     │   ├── api/             # FastAPI routers (upload, query, health)
     │   ├── core/            # Config, database session, logging
     │   ├── models/          # SQLAlchemy ORM models
     │   ├── schemas/         # Pydantic schemas (request/response)
     │   ├── services/        # Business logic: importer, analytics, agent, reporter
     │   ├── bot/             # Telegram bot handler & commands
     │   └── main.py          # FastAPI application entrypoint
     ├── data/
     │   └── synthetic/       # Dataset CSV/XLSX sintetis F&B
     ├── tests/               # Unit & integration tests
     ├── docs/                # Dokumentasi & log progres
     ├── docker-compose.yml   # PostgreSQL & App containers
     ├── Dockerfile           # Backend container
     ├── requirements.txt     # Python dependencies
     └── .env.example         # Template konfigurasi environment
     ```
  2. Susun `requirements.txt` (FastAPI, uvicorn, sqlalchemy, asyncpg, alembic, pydantic-settings, pandas, openpyxl, openai, aiogram/python-telegram-bot, apscheduler).
  3. Konfigurasi `docker-compose.yml` untuk PostgreSQL 16.
  4. Implementasikan ORM Models sesuai `ARCHITECTURE.md` (Outlets, Categories, Products, Transactions, TransactionItems, ImportJobs).
- **Kriteria Keberhasilan (Definition of Done)**:
  - Database schema berhasil dimigrasi / diinisialisasi.
  - Endpoint `GET /api/health` mengembalikan status `200 OK` dan verifikasi koneksi DB sukses.

---

### Fase 1: Generator Dataset Sintetis F&B & POS Importer Engine
- **Tujuan**: Menyediakan data transaksi realistis untuk pengujian dan membangun engine import CSV/XLSX yang robust.
- **Tugas Spesifik**:
  1. **Dataset Generator (`scripts/generate_synthetic_pos.py`)**:
     - Menghasilkan 1.000+ data transaksi kasir realistis khas cafe/resto Indonesia (contoh: Kopi Susu Aren, Caffe Latte, Croissant, Nasi Goreng, Mie Pedas, Dimsum).
     - Pola transaksi realistis: jam ramai siang (12.00-14.00) dan malam (18.00-21.00), variasi metode pembayaran (QRIS, Tunai, Kartu), diskon, dan status.
     - Menyimpan output dalam format CSV dan XLSX di folder `data/synthetic/`.
  2. **POS Importer Engine (`app/services/importer.py`)**:
     - Parser fleksibel yang memetakan kolom CSV/XLSX ke struktur database.
     - Validasi parsing tanggal, parsing angka, deduplikasi invoice.
     - Menyimpan kategori, produk, transaksi, dan item transaksi secara atomik dalam transaksi database.
     - Endpoint API `POST /api/pos/upload` dengan pengembalian ringkasan impor.
- **Kriteria Keberhasilan**:
  - File CSV/XLSX 1000+ baris berhasil diimpor ke PostgreSQL dalam < 3 detik tanpa duplikasi data.

---

### Fase 2: Sales Analytics Engine (Deterministik)
- **Tujuan**: Membangun modul analitik data cepat dan akurat yang menyediakan metrik bisnis F&B yang siap dipanggil oleh AI Agent atau laporan harian.
- **Tugas Spesifik**:
  1. Implementasi service `app/services/analytics.py` dengan fungsi analitik:
     - `get_sales_overview(start_date, end_date)`: Total GMV, net sales, total pesanan, rata-rata order (AOV).
     - `get_top_products(start_date, end_date, limit=5, sort_by="qty"|"revenue")`: Menu terlaris berdasarkan kuantitas atau total omzet.
     - `get_slow_moving_products(start_date, end_date, limit=5)`: Menu paling sepi peminat untuk warning deadstock/bahan baku terbuang.
     - `get_peak_hours(date)`: Distribusi pesanan dan omzet per jam operasional.
     - `compare_sales_period(current_start, current_end, previous_start, previous_end)`: Komparasi persentase kenaikan/penurunan omzet.
     - `get_payment_method_stats(start_date, end_date)`: Distribusi pembayaran (QRIS, Cash, dll).
  2. Buat unit test untuk memastikan akurasi perhitungan matematis (0% error margin).
- **Kriteria Keberhasilan**:
  - Semua fungsi kalkulasi lolos pengujian unit test dan menghasilkan angka yang 100% cocok dengan data mentah.

---

### Fase 3: AI Agent dengan DeepSeek Tool Calling & Multi-Turn Memory [SELESAI]
- **Tujuan**: Menghubungkan LLM DeepSeek dengan Sales Analytics Engine via Function Calling standar serta mendukung konteks percakapan multi-turn berkelanjutan.
- **Tugas Spesifik**:
  1. Implementasi `app/services/deepseek_agent.py`:
     - Integrasi client API DeepSeek (base URL `https://api.deepseek.com`).
     - Definisi Schema Tools (JSON schema untuk tiap fungsi di Analytics Engine: overview, top products, deadstock, peak hours, komparasi periode, pembayaran, kategori, weekend lift).
     - Tool Dispatcher: Memetakan permintaan fungsi dari LLM ke fungsi eksekusi nyata deterministik di Analytics Engine.
     - **Multi-Turn Conversation Memory**: Integrasi tabel `chat_sessions` dan `chat_messages` dengan sliding window 10 pesan terakhir.
     - **Local Fallback Agent Engine**: Context-aware NLP routing ketika DeepSeek API key belum diisi atau offline, mampu merespons pertanyaan tindak lanjut (*follow-up queries*).
  2. Persona & System Prompt Engineering:
     - Karakter "JuraganAI": Hangat, suportif, berwawasan bisnis kuliner, memberikan saran berbasis data nyata.
     - Melarang keras "berhalusinasi" angka: jika data tidak tersedia, AI dengan jujur menyatakan belum ada data untuk rentang waktu tersebut.
  3. API Endpoints (`app/api/v1/agent.py`):
     - `POST /api/v1/agent/chat`: Menerima query user dan `conversation_id`, mengembalikan balasan dan tool metadata.
     - `GET /api/v1/agent/conversations/{conversation_id}`: Mengambil seluruh riwayat pesan sesi.
     - `DELETE /api/v1/agent/conversations/{conversation_id}`: Mereset memori sesi percakapan.
  4. Web Dashboard UI (`app/templates/dashboard.html`):
     - Session state persistent, badge indikator sesi aktif, dan tombol 🔄 Reset Chat.
- **Kriteria Keberhasilan**:
  - Pertanyaan bahasa alami berhasil memicu pemanggilan tool yang tepat dan menghasilkan jawaban akurat.
  - Percakapan multi-turn mempertahankan konteks interaksi antar-giliran.
  - Seluruh pengujian otomatis `tests/test_agent.py` lulus 100%.

---

### Fase 4: Telegram Bot Interface & Scheduler Laporan Harian [SELESAI]
- **Tujuan**: Membuka channel komunikasi yang paling mudah diakses oleh pemilik F&B via Telegram Bot (@JuragangantengAI_bot).
- **Tugas Spesifik**:
  1. **Telegram Bot Service (`app/bot/telegram_bot.py`)**:
     - Background Long Polling runner terintegrasi pada FastAPI lifespan, mengizinkan interaksi real-time tanpa setup webhook publik.
     - Command `/start`, `/help`, `/omzet`, `/laporan`, dan `/reset`.
     - File Upload Handler: Mendukung impor langsung file `.csv` dan `.xlsx` dari chat Telegram ke database dengan pesan konfirmasi instan.
     - Chat Multi-Turn: Obrolan bebas diteruskan ke DeepSeek Agent dengan context memory `tg_{chat_id}`.
  2. **Automated Daily Reporting Scheduler (`app/services/daily_reporter.py`)**:
     - APScheduler cron job dijadwalkan setiap malam (22:30 WIB) untuk mengirimkan ringkasan omzet, AOV, best-sellers, peak hours, dan actionable insight bisnis.
     - Endpoint `POST /api/v1/analytics/send-daily-report` untuk pengiriman instan on-demand.
     - Proteksi pengiriman pesan Telegram dengan fallback otomatis ke format plain text jika parse mode Markdown bermasalah.
  3. **Web Dashboard Integration (`app/templates/dashboard.html`)**:
     - Tombol **✈️ Kirim Laporan Telegram** dan badge status aktif bot `@JuragangantengAI_bot ↗`.
- **Kriteria Keberhasilan**:
  - Pengguna dapat bertanya via Telegram dan menerima jawaban real-time dari DeepSeek Agent.
  - Laporan penjualan harian sukses terkirim ke Telegram chat ID terkonfigurasi (`2034469547`).
  - Seluruh pengujian otomatis `tests/test_telegram.py` lulus 100%.

---

### Fase 5: Dockerization, Testing & Dokumentasi Final Release [SELESAI]
- **Tujuan**: Memastikan portabilitas sistem, isolasi environment, dan kesiapan evaluasi proyek secara profesional.
- **Tugas Spesifik**:
  1. Finalisasi `Dockerfile` (Python 3.11-slim, EXPOSE 8005, optimized layer caching, non-buffered logging).
  2. Finalisasi `docker-compose.yml` (Service PostgreSQL 16 pada host port 5436 + Backend FastAPI pada port 8005, dengan integrasi `env_file: .env` dan healthcheck db).
  3. Pembuatan `.dockerignore` untuk efisiensi transfer konteks build.
  4. Pengujian otomatis menyeluruh: 19 test Pytest lulus 100% (Analytics, Importer, AI Agent Memory, Telegram Bot & Scheduler).
  5. Penyusunan master dokumentasi `README.md` dan arsip log progres di `docs/progress/`.
- **Kriteria Keberhasilan**:
  - Sistem dapat dijalankan dengan perintah tunggal `docker compose up --build` atau via local runner `python run_dev.py`.
  - Semua metrik bisnis dan fungsionalitas PRD v2.1 terverifikasi 100% bekerja.

---

### Fase 6: AI Architecture, Multi-Provider OpenRouter & Floating Chatbot Widget [SELESAI]
- **Tujuan**: Memperkuat kapabilitas AI Agent dengan adopsi SOP AI Engineer, multi-provider LLM (OpenRouter & DeepSeek) dengan cascading fallback, visual formatting Markdown ke HTML, dan antarmuka Floating Chatbot Widget.
- **Tugas Spesifik**:
  1. **AI Engineer Skill Matrix (`.agent/skills/ai-engineer/SKIIL.md`)**:
     - Standarisasi 5 pilar arsitektur agentic: Grounding & Anti-Hallucination, Tool Calling Schemas, Cascading Multi-Provider Fallback, Sliding Window Memory Management, dan UI Presentation.
  2. **Multi-Provider OpenRouter & DeepSeek (`app/core/config.py` & `app/services/deepseek_agent.py`)**:
     - Penambahan konfigurasi `LLM_PROVIDER`, `OPENROUTER_API_KEY`, `OPENROUTER_BASE_URL`, dan `OPENROUTER_MODEL`.
     - Cascading fallback: Primary provider -> Secondary provider -> Local Deterministic Engine.
     - Temporal anchoring otomatis (hari, tanggal, jam) pada System Prompt.
  3. **Rich Visual Markdown Rendering (`app/templates/dashboard.html`)**:
     - Client-side rendering via `marked.js` dan `DOMPurify` untuk menghilangkan tampilan karakter markdown mentah.
  4. **Floating Pop-up Chatbot Widget (`app/templates/dashboard.html`)**:
     - Tombol FAB melayang di kanan bawah untuk membuka/menutup chat popup secara fleksibel.
     - Reorganisasi layout utama: POS Importer & Dedicated Menu/Deadstock Evaluation Panel.
- **Kriteria Keberhasilan**:
  - Chatbot widget interaktif dapat dibuka/tutup dengan lancar dan merender visual HTML rapi.
  - Sistem mendukung pergantian provider LLM ke OpenRouter secara mulus dengan fallback pengaman.
  - 100% unit tests passed.

