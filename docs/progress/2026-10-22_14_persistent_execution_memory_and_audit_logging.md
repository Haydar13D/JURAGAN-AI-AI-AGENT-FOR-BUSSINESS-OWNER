# Catatan Progres Pengembangan: Persistent Execution Memory & History Logging Engine
## Tanggal: 2026-10-22 | Sesi: #14 | Fitur: Persistent Memory, Node Snapshots, & Run Audit Viewer

---

### 1. Ringkasan & Tujuan
- Menggantikan in-memory state volatile Kirata dengan **Persistent Database Storage (SQLite / SQLAlchemy)** yang aman dan permanen.
- Menyediakan sistem **Execution Memory & History Logging** komprehensif sehingga setiap kali alur kerja dijalankan (baik mode `LIVE` maupun `DRY_RUN`):
  1. Header eksekusi (`WorkflowRun`) tersimpan permanen di database.
  2. Seluruh trace langkah node (`NodeRunEvent`) menyimpan **Snapshot Input Memory** dan **Snapshot Output Memory** dalam format JSON terstruktur.
  3. Jejak audit persetujuan aksi manusia (`ActionApproval`) mencatat `reviewer_user_id`, waktu persetujuan, dan status keputusan (`APPROVE` / `REJECT`).
  4. Web UI (`/kirata`) dilengkapi tab navigasi filter riwayat dan modal inspeksi memory interaktif.

---

### 2. Rincian Implementasi

#### A. Arsitektur Repository (`app/services/workflow_repository.py`)
- **`ensure_seed_data()`**: Memastikan entitas `Tenant` default (`tenant_juragan_retail`, `tenant_fnb`) dan katalog `WorkflowTemplate` terdaftar di database.
- **`save_workflow()`**: Menyimpan definisi alur kerja kanonikal dan versi immutable ke tabel `workflows` dan `workflow_versions`.
- **`save_run_record()`**: Menyimpan status run, input data CSV, dan seluruh event node beserta snapshot data input/output ke tabel `workflow_runs` dan `node_run_events`.
- **`record_action_decision()`**: Mencatat audit keputusan pemilik usaha ke tabel `action_approvals` dan memperbarui status run menjadi `COMPLETED` atau `REJECTED`.
- **`list_runs()` & `get_run_detail()`**: Endpoint query riwayat run dengan filter dinamis (`tenant_id`, `workflow_id`, `status`, `run_mode`, `limit`).

#### B. Perekaman Memory Snapshot pada Runtime (`app/services/workflow_runner.py`)
- Menambahkan metode `_serialize_snapshot()` yang membersihkan objek data internal menjadi dictionary serializable JSON.
- Merekam `input_snapshot` (data hasil input binding yang diterima tool) dan `output_snapshot` (data kalkulasi yang diproduksi tool) pada setiap langkah node di `run_dry_run()` dan `run_live()`.

#### C. Integrasi API REST (`app/api/v1/workflows.py`)
- `GET /api/v1/workflows/runs`: Mendukung filter query `?workflow_id=...&status=...&run_mode=...`.
- `GET /api/v1/workflows/runs/{run_id}/events`: Mengembalikan snapshot memory lengkap dan jejak audit reviewer.
- `GET /api/v1/workflows/{workflow_id}/runs`: Riwayat run spesifik per alur kerja.
- Persistensi otomatis saat alur kerja dibuat (`from-template`), disetujui konfigurasi (`approve`), dijalankan (`runs`), atau disetujui aksi bisnisnya (`decision`).

#### D. Visual Memory & Log Inspector Web UI (`app/templates/kirata_builder.html`)
- **Filter Toolbar di Halaman Run**: Filter instan untuk `Semua Run`, `⚡ Live Runs`, `🧪 Dry-Runs`, dan `🔒 Menunggu Approval`.
- **Modal Audit Memory Trace**:
  - Ringkasan eksekusi: Workflow Name, Version, Total Execution Time (ms), Waktu Mulai, dan Reviewer ID.
  - Accordion interaktif per step node dengan tombol `👁️ Memory` yang membuka viewer JSON terformat untuk **`📥 Input Snapshot Memory`** dan **`📤 Output Snapshot Memory`**.
  - Akses langsung unduh file katalog CSV hasil akhir.

---

### 3. Hasil Pengujian & Verifikasi (100% PASS)

1. **Automated Unit & Integration Test**:
   - `tests/test_workflow_memory.py`: **PASSED** (Verifikasi tabel `workflow_runs`, `node_run_events`, `action_approvals`, snapshot input/output, dan REST API filter).
   - Seluruh test suite (19 test scenarios): **19 PASSED in 22.78s**
     - `test_workflow_memory.py`
     - `test_fnb_workflow.py`
     - `test_kirata_api.py`
     - `test_kirata_workflow.py`

2. **Browser End-to-End Verification**:
   - Filter pills dinamis (`Semua`, `Live`, `Dry-Run`) berfungsi responsif.
   - Inspeksi modal berhasil membuka JSON snapshot `calculate_pricing` dan node lainnya tanpa lag.
   - Bukti tangkapan layar tersimpan: `runs_history_view` dan `run_memory_snapshot_expanded`.
