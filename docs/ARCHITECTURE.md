# Arsitektur Sistem JuraganAI
## Dokumen Desain Teknis & Skema Basis Data

---

## 1. Diagram Arsitektur Tingkat Tinggi (High-Level Architecture)

```
       +-------------------------------------------------------------+
       |                        PENGGUNA (OWNER)                     |
       +------------------------------+------------------------------+
                                      |
                     +----------------+----------------+
                     | (Chat / Laporan Harian)         | (Upload CSV/XLSX)
                     v                                 v
          +-----------------------+         +-----------------------+
          |      TELEGRAM BOT     |         |     WEB DASHBOARD /   |
          |   (Telegram Bot API)  |         |     REST API CLIENT   |
          +-----------+-----------+         +-----------+-----------+
                      |                                 |
                      +---------------+-----------------+
                                      |
                                      v
       +-------------------------------------------------------------+
       |                  FASTAPI BACKEND SERVICE                    |
       |                                                             |
       |  +---------------------+        +------------------------+  |
       |  |  Telegram Controller|        |   POS Import Router    |  |
       |  +----------+----------+        +-----------+------------+  |
       |             |                               |               |
       |             v                               v               |
       |  +---------------------+        +------------------------+  |
       |  |   AI Agent Service  |        |    Data Import Service |  |
       |  |  (Prompt & History) |        |    (Clean & Normalize) |  |
       |  +----------+----------+        +-----------+------------+  |
       |             |                               |               |
       |             v                               v               |
       |  +---------------------+        +------------------------+  |
       |  |  Tool Calling Engine|------->| Sales Analytics Engine |  |
       |  +----------+----------+        +-----------+------------+  |
       |             |                               |               |
       |             |                               v               |
       |             |                  +-------------------------+  |
       |             |                  |  APScheduler / Worker   |  |
       |             |                  |  (Daily Cron Reporter)  |  |
       |             |                  +------------+------------+  |
       +-------------|-------------------------------|---------------+
                     |                               |
          +----------+----------+                    |
          |                     |                    |
          v                     v                    v
+-------------------+ +----------------------------------------------+
|   DEEPSEEK API    | |             POSTGRESQL DATABASE              |
| (LLM Tool Calling)| |  - transactions         - transaction_items  |
|                   | |  - products             - categories         |
|                   | |  - outlets              - import_jobs        |
+-------------------+ +----------------------------------------------+
```

---

## 2. Alur Kerja Komponen Utama (Data Flows)

### 2.1 Alur Import Data POS
1. User mengunggah file CSV/XLSX hasil ekspor dari POS (Moka, Pawoon, Olsera, dll.).
2. **Data Import Service** membaca file via `pandas` / `polars`.
3. Normalisasi kolom (mapping dinamis nama kolom ke standar JuraganAI).
4. Pembersihan data: validasi tipe data tanggal, penanganan status transaksi (Void, Refund, Completed), serta pembersihan karakter anomali.
5. Inseri batch transaksi ke PostgreSQL (tabel `transactions` dan `transaction_items`).
6. Update status `import_jobs` menjadi `COMPLETED` dan mengembalikan metadata import (jumlah baris, rentang tanggal, total omzet terdeteksi).

### 2.2 Alur Percakapan AI dengan Tool Calling
```
[User Chat] 
    │ ("Menu apa yang paling laku 7 hari terakhir?")
    ▼
[AI Agent Service]
    │ 1. Muat Riwayat Chat + System Prompt JuraganAI
    │ 2. Kirim pesan + Daftar Tools ke DeepSeek API
    ▼
[DeepSeek LLM]
    │ 3. LLM memutuskan memanggil tool:
    │    get_top_products(start_date="2026-10-05", end_date="2026-10-12", limit=5)
    ▼
[Tool Calling Dispatcher]
    │ 4. Eksekusi fungsi analytics di Sales Analytics Engine
    │ 5. Query SQL Agregasi ke PostgreSQL
    ▼
[Sales Analytics Engine]
    │ 6. Mengembalikan JSON data konkret:
    │    [{"name": "Kopi Susu Gula Aren", "qty": 340, "total": 6800000}, ...]
    ▼
[DeepSeek LLM]
    │ 7. Terima hasil tool -> sintesis respons ramah & insight bisnis F&B
    ▼
[User Chat Response]
    ("Halo Juragan! 7 hari terakhir ini menu terlaris adalah Kopi Susu Gula Aren 
      (340 cup terjual, omzet Rp6.800.000). Saran: stok sirup aren aman ya, 
      dan bisa dibuatkan paket bundling dengan croissant!")
```

### 2.3 Alur Laporan Harian Otomatis (Daily Scheduled Reporting)
1. **APScheduler** menjalankan cron job setiap pukul 22:30 WIB.
2. Memanggil `SalesAnalyticsEngine.get_daily_summary(today)`.
3. Memanggil `DeepSeekAgent.generate_daily_insight(summary_data)` untuk memberikan rekomendasi bisnis singkat (2-3 kalimat).
4. Memformat pesan menggunakan template Markdown Telegram yang rapi (lengkap dengan emoji indikator performa).
5. Mengirimkan pesan melalui **Telegram Bot API** ke `chat_id` pemilik outlet.

---

## 3. Desain Skema Basis Data (Database Schema)

```sql
-- 1. OUTLETS (Multi-tenant ready)
CREATE TABLE outlets (
    id SERIAL PRIMARY KEY,
    name VARCHAR(150) NOT NULL,
    address TEXT,
    telegram_chat_id BIGINT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 2. CATEGORIES
CREATE TABLE categories (
    id SERIAL PRIMARY KEY,
    outlet_id INT REFERENCES outlets(id) ON DELETE CASCADE,
    name VARCHAR(100) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_outlet_category UNIQUE (outlet_id, name)
);

-- 3. PRODUCTS
CREATE TABLE products (
    id SERIAL PRIMARY KEY,
    outlet_id INT REFERENCES outlets(id) ON DELETE CASCADE,
    category_id INT REFERENCES categories(id) ON DELETE SET NULL,
    sku VARCHAR(100),
    name VARCHAR(200) NOT NULL,
    base_price NUMERIC(14, 2) NOT NULL DEFAULT 0,
    cost_price NUMERIC(14, 2) DEFAULT 0, -- COGS (Harga Pokok Penjualan) jika ada
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_outlet_product UNIQUE (outlet_id, name)
);

-- 4. TRANSACTIONS
CREATE TABLE transactions (
    id BIGSERIAL PRIMARY KEY,
    outlet_id INT REFERENCES outlets(id) ON DELETE CASCADE,
    invoice_number VARCHAR(100) NOT NULL,
    transaction_time TIMESTAMP WITH TIME ZONE NOT NULL,
    subtotal NUMERIC(14, 2) NOT NULL DEFAULT 0,
    discount NUMERIC(14, 2) NOT NULL DEFAULT 0,
    tax NUMERIC(14, 2) NOT NULL DEFAULT 0,
    service_charge NUMERIC(14, 2) NOT NULL DEFAULT 0,
    total_amount NUMERIC(14, 2) NOT NULL DEFAULT 0,
    payment_method VARCHAR(50), -- e.g. QRIS, CASH, DEBIT, GO-PAY
    status VARCHAR(30) NOT NULL DEFAULT 'COMPLETED', -- COMPLETED, VOID, REFUND
    cashier_name VARCHAR(100),
    raw_reference JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_outlet_invoice UNIQUE (outlet_id, invoice_number)
);

-- Index performa query analitik waktu
CREATE INDEX idx_transactions_time ON transactions (outlet_id, transaction_time);
CREATE INDEX idx_transactions_status ON transactions (status);

-- 5. TRANSACTION_ITEMS
CREATE TABLE transaction_items (
    id BIGSERIAL PRIMARY KEY,
    transaction_id BIGINT REFERENCES transactions(id) ON DELETE CASCADE,
    product_id INT REFERENCES products(id) ON DELETE SET NULL,
    product_name VARCHAR(200) NOT NULL,
    quantity INT NOT NULL DEFAULT 1,
    unit_price NUMERIC(14, 2) NOT NULL DEFAULT 0,
    discount NUMERIC(14, 2) NOT NULL DEFAULT 0,
    subtotal NUMERIC(14, 2) NOT NULL DEFAULT 0,
    notes TEXT
);

CREATE INDEX idx_items_trans_id ON transaction_items (transaction_id);
CREATE INDEX idx_items_prod_name ON transaction_items (product_name);

-- 6. IMPORT_JOBS (Audit trail import data POS)
CREATE TABLE import_jobs (
    id SERIAL PRIMARY KEY,
    outlet_id INT REFERENCES outlets(id) ON DELETE CASCADE,
    filename VARCHAR(255) NOT NULL,
    file_type VARCHAR(20) NOT NULL, -- CSV, XLSX
    status VARCHAR(30) NOT NULL DEFAULT 'PENDING', -- PENDING, PROCESSING, COMPLETED, FAILED
    total_rows INT DEFAULT 0,
    imported_rows INT DEFAULT 0,
    error_message TEXT,
    started_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP WITH TIME ZONE
);
```

---

## 4. Keamanan & Konfigurasi Lingkungan
Variabel lingkungan yang dibutuhkan (`.env`):
- `DATABASE_URL`: Koneksi PostgreSQL (contoh: `postgresql+asyncpg://juragan:secret@db:5432/juragan_ai`)
- `DEEPSEEK_API_KEY`: API Key dari platform DeepSeek.
- `DEEPSEEK_BASE_URL`: `https://api.deepseek.com`
- `DEEPSEEK_MODEL`: `deepseek-chat`
- `TELEGRAM_BOT_TOKEN`: Token bot dari `@BotFather`.
- `DEFAULT_OUTLET_ID`: ID outlet default untuk tahap MVP (1).
- `DAILY_REPORT_CRON`: Waktu pengiriman laporan harian (contoh: `22:30`).
- `APP_ENV`: `development` / `production`.
