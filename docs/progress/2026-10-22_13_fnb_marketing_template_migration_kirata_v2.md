# Catatan Progres Pengembangan: Migrasi Template F&B Sales-to-Marketing ke Kirata AI Workflow Builder (v2.0)
## Tanggal: 2026-10-22 | Sesi: #13 | Fitur: Migrasi FR-16 (Use Case Kedua Resmi Platform Kirata)

---

### 1. Ringkasan & Tujuan
- Memigrasikan use case awal JuraganAI (F&B Sales-to-Marketing: Ingestion POS, Deteksi Opportunity, Perencanaan Kampanye, Flyer Generator AI, Approval Owner, dan Publikasi Instagram) menjadi **Template Resmi ke-2** di platform **Kirata AI Workflow Builder** (`tpl_fnb_sales_to_marketing_v1`).
- Mengubah arsitektur modul F&B lama yang kaku menjadi **arsitektur Tool Registry modular dan aman**, tunduk pada:
  1. Kontrak `BaseTool` kanonikal dengan skema Pydantic (`input_schema`, `output_schema`, `config_schema`).
  2. DAG Workflow Definition kanonikal 6 node dengan topological ordering.
  3. Two-Tier Human Approval: Approval Konfigurasi (Tier 1) dan Approval Aksi Publikasi Instagram (Tier 2).
  4. Kompatibilitas penuh dengan AI Natural Language Proposal Generator (DeepSeek / AtmoRouter) dan isolated dry-run engine.

---

### 2. Rincian Implementasi

#### A. Tool Registry Resmi F&B (`app/services/fnb_marketing/`)
1. **`app/services/fnb_marketing/schemas.py`**:
   - Skema Pydantic kuat untuk data pipeline F&B: `PosDataSummary`, `ItemSalesStats`, `OpportunityItem`, `BatchOpportunities`, `CampaignPlanItem`, `BatchCampaignPlans`, `FlyerAssetItem`, `BatchFlyerAssets`, `ApprovedCampaignPayload`, `PublishResult`.
2. **`app/services/fnb_marketing/tools.py`**:
   - `fnb.ingest_pos_data` (Ingestion): Membaca riwayat transaksi POS kafe/resto dan menghitung statistik penjualan 30–60 hari.
   - `fnb.detect_opportunities` (Analysis): Mendeteksi menu yang mengalami penurunan penjualan melebihi batas toleransi (`sales_drop_threshold_percent`).
   - `fnb.plan_campaign` (Transformation): Merancang paket promo menu, diskon persentase, dan durasi kampanye.
   - `fnb.generate_flyer` (Analysis): Memproduksi copywriting caption media sosial dan URL preview asset visual flyer kampanye.
   - `governance.action_approval` (Governance): Gate persetujuan pemilik usaha sebelum campaign disiarkan ke media sosial publik.
   - `fnb.publish_instagram` (Export - Side Effect): Mempublikasikan materi promosi dan flyer ke Instagram Feed & Stories.
3. **`app/services/fnb_marketing/__init__.py`**:
   - Fungsi registrasi `register_fnb_tools()` ke dalam singleton `ToolRegistry`.

#### B. Template Kanonikal & Template Catalog (`data/fixtures/` & `app/services/template_catalog.py`)
- **`data/fixtures/fnb_marketing_template.json`**:
  - Definisi workflow 6 node kanonikal lengkap dengan binding output/input antar node.
- **`app/services/template_catalog.py`**:
  - Mengubah status `tpl_fnb_sales_to_marketing_v1` dari `"LATER"` menjadi `"AVAILABLE"`.
  - Mengonfigurasi parameter onboarding dinamis: `outlet_name`, `sales_drop_threshold_percent`, `default_promo_discount_percent`, `promo_duration_days`, dan `target_audience`.

#### C. Generalisasi Engine Workflow & Approval
- **`app/services/workflow_runner.py`**:
  - Mengabstraksi `action_type` pada `ActionApprovalRequest` sehingga mendukung `INSTAGRAM_CAMPAIGN_PUBLISH` maupun `CATALOG_PUBLISH_OR_EXPORT`.
  - Menghubungkan output upstream secara fleksibel ke payload approval.
- **`app/services/retail_pricing/tools.py`**:
  - Memperbarui `OwnerActionApprovalTool` agar menerima input universal (`catalog`, `campaign_payload`, atau generic `payload`).
- **`app/services/proposal_generator.py`**:
  - Mengupdate rule-based parser dan AtmoRouter prompt agar mengenali intent F&B (diskon promo %, drop threshold %, durasi kampanye).

#### D. Peningkatan UI Kirata Workflow Builder (`app/templates/kirata_builder.html`)
- Memperbaiki form onboarding modal agar mendukung field bertipe teks/string (`target_audience`, `outlet_name`) di samping field angka numerik.
- Menjadikan Action Approval modal dinamis:
  - Untuk F&B: Menampilkan tabel preview materi promo, headline, caption & hook, diskon %, asset flyer, dan tombol `🚀 Setujui & Publish ke Instagram`.
  - Untuk Retail: Menampilkan tabel review harga iPhone bekas dan tombol `🚀 Setujui & Ekspor Katalog CSV`.

---

### 3. Hasil Pengujian Lengkap (100% PASS)

Semua pengujian unit dan integrasi lulus tanpa error:
- **`tests/test_fnb_workflow.py`** (5 Scenarios):
  - `test_fnb_template_loading_and_dag_validation`: **PASSED**
  - `test_fnb_dry_run_execution`: **PASSED** (Semua 6 trace node tereksekusi tanpa efek samping)
  - `test_fnb_live_run_action_approval_lifecycle`: **PASSED** (Tertahan pada `WAITING_APPROVAL`, lalu mempublikasikan ke Instagram setelah di-approve)
  - `test_fnb_ai_proposal_patch`: **PASSED** (Modifikasi diskon promo via AI natural language proposal)
  - `test_fnb_api_full_journey`: **PASSED** (Pengujian siklus hidup end-to-end via REST API)
- **`tests/test_kirata_api.py`** (3 Scenarios): **PASSED**
- **`tests/test_kirata_workflow.py`** (10 Scenarios): **PASSED**

**Total: 18 passed in 27.74s**

---

### 4. Status Platform Saat Ini
Platform Kirata AI Workflow Builder v2.0 kini memiliki **2 Template Bisnis Siap Pakai (AVAILABLE)**:
1. 📱 **Retail Pricing — iPhone Bekas**: Pipeline ingest vendor, normalisasi, kalkulasi margin, price intelligence, review guardrails, human approval, dan ekspor katalog CSV.
2. ☕ **F&B Sales-to-Marketing**: Pipeline ingest transaksi POS, deteksi menu drop, campaign planning promo, flyer copy & asset generator, human approval gate, dan publikasi Instagram.
