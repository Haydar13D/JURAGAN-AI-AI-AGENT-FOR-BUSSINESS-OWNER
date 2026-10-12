# Product Requirements Document (PRD) v2.1
## JuraganAI — AI Business Intelligence Assistant untuk Pemilik Usaha F&B

---

## 1. Ringkasan Eksekutif (Executive Summary)
**JuraganAI** adalah asisten Business Intelligence (BI) berbasis Artificial Intelligence yang dirancang khusus untuk pemilik usaha Food & Beverage (F&B) — mulai dari coffee shop, kedai, resto, hingga cloud kitchen — yang telah menggunakan sistem Point of Sale (POS). 

Mayoritas pemilik UMKM F&B memiliki data transaksi yang melimpah dari POS (seperti Moka, Pawoon, Olsera, Majoo, ESB), namun data tersebut pasif dan jarang dianalisis secara mendalam karena keterbatasan waktu dan keahlian analitik data (Excel/Spreadsheet). **JuraganAI** hadir sebagai jembatan yang mengubah data mentah POS menjadi wawasan bisnis (actionable insights) yang dapat diakses secara instan melalui percakapan alami (chat AI) dan laporan harian otomatis di Telegram.

---

## 2. Masalah & Peluang (Problem Statement & Opportunity)

### 2.1 Masalah Pengguna (Pain Points)
1. **Data Silo & Pasif**: Data penjualan tersimpan di dashboard POS tanpa memberikan rekomendasi aksi bisnis yang praktis.
2. **Keterbatasan Waktu & Analisis**: Pemilik F&B sibuk dengan operasional harian (dapur, staf, bahan baku), tidak punya waktu untuk mengekspor CSV dan membuat pivot table.
3. **Keputusan Berdasarkan 'Feeling'**: Keputusan stok bahan, rotasi menu, diskon/promo, dan penjadwalan staf sering diambil tanpa dasar data akurat.
4. **Laporan Penjualan Lambat**: Laporan manual dari kasir atau manajer toko sering terlambat, tidak terstandarisasi, atau tidak informatif.

### 2.2 Solusi JuraganAI
- **Import Data Mandiri & Cepat**: Upload file CSV/XLSX export dari POS secara langsung.
- **Sales Analytics Engine Handal**: Menghitung metrik penting secara instan (GMV, AOV/Basket Size, Top/Bottom Items, Peak Hours, Margin).
- **AI Agent Chat Berbasis Data (Tool Calling)**: Pemilik bisa bertanya dengan bahasa sehari-hari: *"Menu apa yang paling laku minggu ini?"*, *"Bandingkan omzet Jumat ini dengan Jumat lalu"*, *"Kapan jam paling ramai di outlet?"*. AI menjawab berdasarkan data aktual melalui fungsi (tools), bukan halusinasi.
- **Laporan Harian Otomatis via Telegram**: Setiap malam setelah toko tutup, bot mengirimkan ringkasan omzet, menu terlaris, anomali penjualan, dan rekomendasi praktis untuk esok hari.

---

## 3. Persona Pengguna (User Persona)
- **Nama**: Mas Budi / Mbak Rina
- **Peran**: Owner / Pengelola Outlet Cafe & Resto (1–3 cabang)
- **Karakteristik**:
  - Menggunakan smartphone sebagai alat kerja utama.
  - Menggunakan POS modern (Moka / Pawoon / Majoo / dll.).
  - Menginginkan informasi cepat, padat, dan solutif.
  - Komunikasi harian melalui aplikasi chat (Telegram / WhatsApp).

---

## 4. Cakupan Produk MVP (Minimum Viable Product Scope)

Untuk rilis MVP, pengembangan difokuskan pada **4 Pilar Utama**:

```
+-------------------------------------------------------------------------+
|                                JURAGAN AI MVP                           |
+-------------------+--------------------+-------------------+------------+
| 1. POS Data Import| 2. Sales Analytics | 3. AI Agent Tool  | 4. Auto Daily|
|    (CSV / XLSX)   |    Engine          |    Calling        |    Reporting |
|                   |                    |    (DeepSeek API) |  (Telegram)|
+-------------------+--------------------+-------------------+------------+
```

### Pilar 1: POS Data Import (CSV / XLSX)
- **Fungsi**: Mengimpor data transaksi mentah yang diekspor dari POS.
- **Fitur Utama**:
  - Upload file melalui API / UI / Bot.
  - Schema normalization (pemetaan kolom: Tanggal/Waktu, No. Struk/Order ID, Nama Menu, Kategori, Qty, Harga Satuan, Diskon, Total Bayar, Metode Pembayaran).
  - Data cleaning & validasi (penanganan baris kosong, status void/refund, deduplikasi).
  - Dukungan format POS populer (dimulai dengan format generik terstandarisasi & Moka/Pawoon style).

### Pilar 2: Sales Analytics Engine
- **Fungsi**: Mesin komputasi analitik deterministik yang menghitung metrik bisnis dari database.
- **Metrik Utama**:
  - **Financial**: Gross Merchandise Value (GMV), Net Sales, Diskon, Pajak.
  - **Volume & Basket Size**: Total Order, Total Items Sold, Average Order Value (AOV).
  - **Product Performance**: Top 5 / Top 10 Best Sellers, Menu Slow-Moving (Deadstock warning).
  - **Time Analysis**: Peak Hours (distribusi transaksi per jam), performa hari kerja vs akhir pekan.
  - **Payment Breakdown**: Persentase QRIS, Tunai, EDC/Kartu, Transfer.
  - **Period Comparison**: Perbandingan hari ini vs kemarin, minggu ini vs minggu lalu.

### Pilar 3: AI Agent dengan Tool Calling
- **Fungsi**: Otak percakapan cerdas yang menghubungkan bahasa alami pengguna dengan data analitik.
- **Teknologi**: DeepSeek API (DeepSeek-Chat / V3) menggunakan format OpenAI Tool Calling (Function Calling).
- **Karakter AI (System Persona)**: "JuraganAI" — konsultan bisnis F&B yang ramah, santun, lugas, menguasai istilah bisnis F&B Indonesia, dan selalu menyertakan angka konkret serta tips actionable.
- **Katalog Tools AI**:
  1. `get_sales_overview(start_date, end_date)`
  2. `get_top_products(start_date, end_date, limit, sort_by)`
  3. `get_peak_hours(date)`
  4. `compare_sales_period(period_a_start, period_a_end, period_b_start, period_b_end)`
  5. `get_payment_method_stats(start_date, end_date)`
  6. `get_slow_moving_products(start_date, end_date, threshold)`

### Pilar 4: Automated Daily Sales Reporting
- **Fungsi**: Pengiriman laporan berkala secara proaktif tanpa perlu ditanya oleh pengguna.
- **Teknologi**: Scheduler (APScheduler / Celery Beat) + Telegram Bot API.
- **Jadwal**: Pukul 22:30 WIB (atau waktu penutupan kasir yang ditentukan).
- **Format Laporan**:
  - Header: Tanggal & Nama Outlet.
  - Highlight Finansial: Total Omzet, Total Transaksi, AOV.
  - Highlight Menu: 3 Menu Terlaris hari ini.
  - Highlight Jam Ramai: Puncak transaksi.
  - Rekomendasi JuraganAI: 1-2 saran singkat untuk operasional esok hari.

---

## 5. Non-Functional Requirements (NFR)
- **Akurasi Data**: Data finansial dan kuantitas penjualan harus 100% akurat dari Sales Analytics Engine (AI dilarang mengarang angka tanpa tool calling).
- **Response Time**: Respon chat AI dengan tool calling selesai dalam rentang waktu < 4 detik.
- **Keamanan Data**: Data penjualan milik tiap outlet terisolasi (Multi-tenant ready / Tenant isolation by `outlet_id`).
- **Skalabilitas**: Backend dirancang asinkron (FastAPI async) dengan basis data PostgreSQL.
- **Containerization**: Seluruh stack dapat dijalankan dengan sekali perintah `docker compose up`.

---

## 6. Arsitektur Teknologi (Tech Stack)
| Komponen | Pilihan Teknologi | Alasan Pemilihan |
|---|---|---|
| **Backend Framework** | FastAPI (Python 3.11+) | Asynchronous, performa tinggi, dokumentasi OpenAPI otomatis, ekosistem AI/Data Python yang sangat kaya. |
| **Database** | PostgreSQL | Handal, ACID-compliant, mendukung query analitik kompleks, agregasi waktu, dan JSONB. |
| **ORM / Migration** | SQLAlchemy 2.0 (Async) & Alembic | Modern async ORM dengan manajemen skema yang teruji. |
| **AI LLM Engine** | DeepSeek API (V3 / Chat) | Cost-effective, pemahaman bahasa sangat kuat, mendukung tool/function calling standar industri. |
| **Interface Bot** | Telegram Bot API (aiogram 3.x / python-telegram-bot) | Ringan, gratis, mudah digunakan di smartphone, mendukung webhook & rich formatting. |
| **Task / Scheduler** | APScheduler / Celery + Redis | Penjadwalan laporan harian otomatis dan background tasks untuk pemrosesan file besar. |
| **Deployment / Env** | Docker & Docker Compose | Kemudahan replikasi environment pengembangan dan produksi. |

---

## 7. Roadmap & Milestone MVP
- **Milestone 1**: Desain Arsitektur, Skema Database, dan Dokumentasi Teknis.
- **Milestone 2**: Dataset Sintetis Transaksi F&B (Generator CSV 1000+ transaksi untuk testing).
- **Milestone 3**: Backend Core & POS Importer Engine (Normalizer CSV/XLSX).
- **Milestone 4**: Sales Analytics Engine (Query kalkulasi metrik bisnis).
- **Milestone 5**: Integrasi DeepSeek Tool Calling & AI Agent Brain.
- **Milestone 6**: Telegram Bot Interface & Automated Daily Reporting Scheduler.
- **Milestone 7**: Pengujian End-to-End, Evaluasi Akurasi, dan Dokumentasi Akhir.
