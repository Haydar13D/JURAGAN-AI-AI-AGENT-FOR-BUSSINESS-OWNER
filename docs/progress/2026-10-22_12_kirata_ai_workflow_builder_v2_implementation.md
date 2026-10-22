# Catatan Progres Pengembangan: Kirata AI Workflow Builder (v2.0)
## Tanggal: 2026-10-22 | Sesi: #12 | Fase: Fase 0 - Fase 4 Implementasi Lengkap Kirata

---

### 1. Tujuan Sesi / Iterasi
- Mengimplementasikan seluruh rencana kerja dari **PRD Kirata AI Workflow Builder v2.0**:
  1. Canonical Workflow Definition & Typed Patch Schemas (Pydantic).
  2. Certified Tool Registry Contract & Allowlist Engine.
  3. Server-side DAG Validator (anti-cycle, schema verification, reachability, allowlist).
  4. Typed Patch Engine & Visual Diff generator.
  5. 9 Tool Tersertifikasi Vertical Retail iPhone Bekas (`retail_pricing`).
  6. Execution & Isolated Dry-Run Runner dengan Two-Tier Human Approval.
  7. AI Natural Language to Typed Patch Compiler (DeepSeek / AtmoRouter v4.1).
  8. REST API Endpoints lengkap (`/templates`, `/from-template`, `/proposals`, `/dry-run`, `/approve`, `/runs`, `/actions/decision`).
  9. Visual Review First Web UI (`/kirata`).
  10. Automated Test Suite 10/10 Acceptance Scenarios PASS.

---

### 2. Pekerjaan yang Selesai Dikerjakan (Done)
- [x] **`app/schemas/workflow.py`**: Model kanonikal `WorkflowDefinition`, `WorkflowNode`, `WorkflowEdge`, `ApprovalPolicy`, `PatchOperation`, `WorkflowProposal`, `DryRunResult`.
- [x] **`app/core/tool_registry.py`**: Framework `BaseTool`, `ToolMetadata`, `ToolCategory`, dan singleton `ToolRegistry`.
- [x] **`app/models/workflow.py`**: Model ORM SQLAlchemy untuk `tenants`, `workflows`, `workflow_versions`, `workflow_runs`, `node_run_events`, `action_approvals`, `vendor_imports`.
- [x] **Fixtures Sintetis**:
  - `data/fixtures/vendor_raw_samples.csv` (25 baris iPhone 11–15, variasi inter/iBox, grade, baris anomali).
  - `data/fixtures/market_references.json` (22 data referensi pasar terkurasi + kasus data stale).
  - `data/fixtures/retail_pricing_template.json` (Template kanonikal 9 node alur kerja retail).
- [x] **`app/services/workflow_validator.py`**: Validator DAG berbasis algoritma Kahn, deteksi cycle, reachability binding, tool allowlist, dan governance guardrail.
- [x] **`app/services/patch_engine.py`**: Menerapkan patch atomik dan memproduksi visual diff (`ADDED`, `MODIFIED`, `UNCHANGED`).
- [x] **`app/services/retail_pricing/`**:
  - `schemas.py`: Kontrak data normalisasi, matching pasar, dan harga.
  - `tools.py`: Implementasi 9 certified tools (`upload_vendor_list`, `extract_and_normalize`, `validate_attributes`, `match_market_references`, `calculate_pricing`, `compare_to_market`, `conditional_review_flag`, `owner_action_approval`, `export_catalog`).
- [x] **`app/services/workflow_runner.py`**: Engine eksekusi topological DAG dengan isolated dry-run dan live pause-and-resume approval.
- [x] **`app/services/proposal_generator.py`**: Penerjemah prompt bahasa Indonesia ke typed patch menggunakan AtmoRouter / DeepSeek dengan mode JSON aman.
- [x] **`app/api/v1/workflows.py`**: REST API endpoints komprehensif.
- [x] **`app/templates/kirata_builder.html`**: Visual Review Web Interface di route `/kirata`.
- [x] **`tests/test_kirata_workflow.py`**: 10 unit tests acceptance PRD v2.0 lulus 100%.

---

### 3. Kendala & Solusi (Issues & Resolutions)
- **Kendala**: Respon LLM AtmoRouter terkadang memiliki teks pembuka/penutup ekstra di luar format JSON murni (`Extra data: char 558`).
- **Solusi**: Diimplementasikan `_extract_json_object()` dengan regex matching outer `{ ... }` dan `json.JSONDecoder().raw_decode()` sehingga parsing JSON kebal terhadap obrolan ekstra LLM.

---

### 4. Hasil Pengujian / Verifikasi
- **Unit Test Suite Kirata (`tests/test_kirata_workflow.py`)**:
  - `test_template_loading_and_pydantic_schema`: **PASSED**
  - `test_dag_cycle_detection_rejection`: **PASSED**
  - `test_unapproved_tool_rejection`: **PASSED**
  - `test_missing_approval_gate_safety_violation`: **PASSED**
  - `test_patch_engine_margin_update`: **PASSED**
  - `test_deterministic_pricing_math`: **PASSED**
  - `test_dry_run_execution`: **PASSED** (25 baris diproses, 9 trace node selesai)
  - `test_two_tier_action_approval_lifecycle`: **PASSED** (Berhenti pada `WAITING_APPROVAL` tanpa persetujuan, lalu mengekspor 22 baris saat disetujui)
  - `test_ai_proposal_supported_request`: **PASSED**
  - `test_ai_proposal_unsupported_marketplace_rejection`: **PASSED** (Tolak halus scraper tanpa membuat tool fiktif)
  - **Hasil**: **10 PASSED in 7.54s**

---

### 5. Langkah Selanjutnya (Next Steps)
- [ ] Menyajikan demo visual web builder `/kirata` kepada owner.
- [ ] Menambahkan template kedua (F&B Marketing Automation) ke dalam `workflow_templates`.
