# Spesifikasi REST API & DeepSeek Tool Definitions
## JuraganAI — AI Agent Interface

---

## 1. Spesifikasi REST API (FastAPI)

### 1.1 Endpoint POS Data Import
- **POST** `/api/v1/pos/import`
  - **Deskripsi**: Mengunggah file CSV atau XLSX export data penjualan POS.
  - **Content-Type**: `multipart/form-data`
  - **Body Parameter**:
    - `file`: UploadFile (CSV / XLSX)
    - `outlet_id`: integer (default: 1)
  - **Response (200 OK)**:
    ```json
    {
      "status": "success",
      "data": {
        "job_id": 12,
        "filename": "pos_export_oktober_2026.csv",
        "total_rows": 1250,
        "imported_transactions": 840,
        "date_range": {
          "start": "2026-10-01T08:00:00",
          "end": "2026-10-12T22:00:00"
        },
        "total_revenue": 24500000.00
      }
    }
    ```

### 1.2 Endpoint Sales Analytics
- **GET** `/api/v1/analytics/overview`
  - **Query Params**: `outlet_id`, `start_date`, `end_date`
  - **Response (200 OK)**:
    ```json
    {
      "period": {"start": "2026-10-11", "end": "2026-10-11"},
      "total_gmv": 3250000.00,
      "total_transactions": 115,
      "average_order_value": 28260.87,
      "total_items_sold": 198
    }
    ```

- **GET** `/api/v1/analytics/top-products`
  - **Query Params**: `outlet_id`, `start_date`, `end_date`, `limit` (default: 5), `sort_by` ("qty" atau "revenue")

- **GET** `/api/v1/analytics/peak-hours`
  - **Query Params**: `outlet_id`, `date` (default: today)

### 1.3 Endpoint AI Agent Chat
- **POST** `/api/v1/agent/chat`
  - **Content-Type**: `application/json`
  - **Body**:
    ```json
    {
      "outlet_id": 1,
      "message": "Berapa omzet hari ini dan menu apa yang paling laku?",
      "conversation_id": "optional-uuid"
    }
    ```
  - **Response (200 OK)**:
    ```json
    {
      "reply": "Halo Juragan! ☕ Hari ini omzet outlet mencapai Rp3.250.000 dari 115 transaksi (rata-rata per nota Rp28.260). Menu paling laris hari ini adalah:\n1. Kopi Susu Gula Aren (42 cup)\n2. Dimsum Ayam (28 porsi)\n3. Croissant Butter (15 pcs)\n\nJam paling ramai terjadi pada pukul 13:00 - 14:00 saat jam makan siang. Mantap sekali performanya hari ini!",
      "tools_executed": [
        "get_sales_overview",
        "get_top_products",
        "get_peak_hours"
      ]
    }
    ```

---

## 2. Definisi Tool Calling DeepSeek (Function Calling Schemas)

Berikut adalah skema tools yang dikirimkan ke DeepSeek API dalam parameter `tools`:

### Tool 1: `get_sales_overview`
```json
{
  "type": "function",
  "function": {
    "name": "get_sales_overview",
    "description": "Mengambil ringkasan data finansial penjualan outlet dalam rentang tanggal tertentu, meliputi total omzet (GMV), jumlah transaksi, dan Average Order Value (AOV).",
    "parameters": {
      "type": "object",
      "properties": {
        "start_date": {
          "type": "string",
          "description": "Tanggal awal periode dalam format YYYY-MM-DD"
        },
        "end_date": {
          "type": "string",
          "description": "Tanggal akhir periode dalam format YYYY-MM-DD"
        }
      },
      "required": ["start_date", "end_date"]
    }
  }
}
```

### Tool 2: `get_top_products`
```json
{
  "type": "function",
  "function": {
    "name": "get_top_products",
    "description": "Mengambil daftar menu makanan/minuman terlaris berdasarkan kuantitas terjual (qty) atau total omzet dalam periode tertentu.",
    "parameters": {
      "type": "object",
      "properties": {
        "start_date": {
          "type": "string",
          "description": "Tanggal awal periode (YYYY-MM-DD)"
        },
        "end_date": {
          "type": "string",
          "description": "Tanggal akhir periode (YYYY-MM-DD)"
        },
        "limit": {
          "type": "integer",
          "description": "Jumlah produk teratas yang ingin diambil (default: 5)"
        },
        "sort_by": {
          "type": "string",
          "enum": ["qty", "revenue"],
          "description": "Urutkan berdasarkan kuantitas terjual ('qty') atau nominal omzet ('revenue')"
        }
      },
      "required": ["start_date", "end_date"]
    }
  }
}
```

### Tool 3: `get_peak_hours`
```json
{
  "type": "function",
  "function": {
    "name": "get_peak_hours",
    "description": "Menganalisis jam-jam paling ramai (peak hours) dan paling sepi pada tanggal tertentu berdasarkan jumlah transaksi per jam.",
    "parameters": {
      "type": "object",
      "properties": {
        "date": {
          "type": "string",
          "description": "Tanggal yang ingin dianalisis jam ramainya (YYYY-MM-DD)"
        }
      },
      "required": ["date"]
    }
  }
}
```

### Tool 4: `compare_sales_period`
```json
{
  "type": "function",
  "function": {
    "name": "compare_sales_period",
    "description": "Membandingkan performa penjualan antara dua periode waktu berbeda (contoh: minggu ini vs minggu lalu, atau kemarin vs hari ini) untuk melihat tren kenaikan atau penurunan.",
    "parameters": {
      "type": "object",
      "properties": {
        "period_a_start": {"type": "string", "description": "Tanggal mulai periode pertama (YYYY-MM-DD)"},
        "period_a_end": {"type": "string", "description": "Tanggal akhir periode pertama (YYYY-MM-DD)"},
        "period_b_start": {"type": "string", "description": "Tanggal mulai periode kedua (YYYY-MM-DD)"},
        "period_b_end": {"type": "string", "description": "Tanggal akhir periode kedua (YYYY-MM-DD)"}
      },
      "required": ["period_a_start", "period_a_end", "period_b_start", "period_b_end"]
    }
  }
}
```

### Tool 5: `get_slow_moving_products`
```json
{
  "type": "function",
  "function": {
    "name": "get_slow_moving_products",
    "description": "Mengidentifikasi menu yang paling sedikit terjual atau hampir tidak terjual sama sekali untuk membantu pemilik F&B mengontrol stok bahan baku dan rotasi menu.",
    "parameters": {
      "type": "object",
      "properties": {
        "start_date": {"type": "string", "description": "Tanggal awal periode (YYYY-MM-DD)"},
        "end_date": {"type": "string", "description": "Tanggal akhir periode (YYYY-MM-DD)"},
        "limit": {"type": "integer", "description": "Jumlah menu paling sepi yang ditampilkan (default: 5)"}
      },
      "required": ["start_date", "end_date"]
    }
  }
}
```
