# Catatan Progres: Fase 2 — Visual Charts & Analisis Penjualan Mendalam
## Tanggal: 2026-10-12 | Sesi: #05 | Fase: Fase 2 (Sales Analytics Engine & Visual Charts)

---

### 1. Tujuan Sesi / Iterasi
- Mengembangkan kemampuan analitik data tingkat lanjut pada **Sales Analytics Engine**:
  1. Analisis tren omzet penjualan harian (*Daily Sales Trend*) berbasis waktu kronologis dan nama hari (Senin - Minggu).
  2. Analisis kontribusi kategori menu (*Category Contribution / Breakdown*) untuk melihat persentase omzet dari Kopi, Makanan, Cemilan, dan Pastry.
  3. Analisis komparasi performa hari kerja (*Weekday*: Senin-Kamis) versus akhir pekan (*Weekend*: Jumat-Minggu) beserta kalkulasi *Weekend Lift Percentage*.
  4. Analisis distribusi *Basket Size* (jumlah menu yang dipesan per nota transaksi).
  5. Integrasi fungsi-fungsi baru tersebut ke dalam katalog *Function Calling* DeepSeek AI Agent.
  6. Visualisasi grafik interaktif modern di Web Dashboard (`http://localhost:8005/`) menggunakan **Chart.js**.

### 2. Pekerjaan yang Selesai Dikerjakan (Done)
- [x] **Skema Pydantic Analitik Lanjutan (`app/schemas/analytics.py`)**:
  - `DailySalesTrendItem` (date, day_name, gmv, transactions, aov)
  - `CategoryContributionItem` (category_name, quantity_sold, total_revenue, percentage_of_revenue)
  - `DayOfWeekAnalysis` (weekday_avg_daily_gmv, weekend_avg_daily_gmv, weekend_lift_percentage, insight)
  - `BasketSizeItem` (basket_size_label, order_count, percentage)
- [x] **Metode Analitik Baru di Sales Analytics Engine (`app/services/analytics_engine.py`)**:
  - `get_daily_sales_trend()`: Menghitung GMV, transaksi, dan AOV harian secara sekuensial.
  - `get_category_contribution()`: Agregasi pendapatan per kategori menu secara proporsional.
  - `get_day_of_week_analysis()`: Menghitung selisih rata-rata omzet hari kerja vs akhir pekan dan menghasilkan insight operasional (misal: rekomendasi penambahan shift barista di akhir pekan).
  - `get_basket_size_distribution()`: Menghitung persentase struk dengan 1 menu, 2 menu, 3-4 menu, atau 5+ menu.
- [x] **Endpoint REST API Baru (`app/api/v1/analytics.py`)**:
  - `GET /api/v1/analytics/daily-trend`
  - `GET /api/v1/analytics/category-breakdown`
  - `GET /api/v1/analytics/day-of-week`
  - `GET /api/v1/analytics/basket-size`
- [x] **Ekspansi Kemampuan AI Agent Tool Calling (`app/services/deepseek_agent.py`)**:
  - Menambahkan tool `get_category_contribution` dan `get_day_of_week_analysis` ke dalam katalog `AGENT_TOOLS`.
  - Menambahkan intent handler otomatis pada *Local Intelligent Fallback Engine* untuk pertanyaan seputar kategori menu dan perbandingan hari kerja/akhir pekan.
- [x] **Visualisasi Dashboard Web Interaktif (`app/templates/dashboard.html`)**:
  - **Grafik Tren Omzet Harian**: Area line chart bergradien amber dengan kurva halus dan tooltip mata uang Rupiah.
  - **Grafik Kontribusi Kategori**: Donut chart multi-warna dengan persentase porsi omzet.
  - **Grafik Distribusi Jam Ramai**: Bar chart interaktif per jam operasional kafe/resto (08:00 - 22:00).
  - **Kartu Analisis Weekend vs Weekday**: Kartu metrik komparasi dengan badge *Weekend Lift (+%)* dan insight otomatis.
  - Tombol prompt cepat AI chat diperbarui untuk menguji pertanyaan analitik kategori dan hari kerja.
- [x] **Pengujian Otomatis (`tests/test_analytics.py`)**:
  - Pengujian menyeluruh endpoint daily-trend, category-breakdown, day-of-week, dan basket-size.

### 3. Kendala & Solusi (Issues & Resolutions)
- **Kendala**: Tampilan dashboard sebelumnya hanya berupa kartu teks tanpa representasi visual grafik tren waktu.
- **Solusi**: Mengintegrasikan Chart.js dengan tema warna gelap yang serasi (amber, emerald, blue, purple) dan responsif terhadap perubahan data atau pengunggahan file transaksi baru.

### 4. Hasil Verifikasi
- Seluruh 14 pengujian unit test pytest lulus 100% (*14 passed*).
- Dashboard di `http://localhost:8005/` secara otomatis merender ketiga grafik (Line, Donut, Bar) dan kartu komparasi hari kerja vs akhir pekan.
