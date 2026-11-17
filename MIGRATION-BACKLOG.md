# MIGRATION-BACKLOG.md — Dari Workflow Engine ke Agent

> Pendamping `AGENTS.md`. Semua aturan di sana berlaku di sini.
> Tujuan: memindahkan "otak" dari graph ke loop, **tanpa membuang** validator, runner,
> versioning, dan approval yang sudah bagus.

## Cara pakai (penting)

**Satu sesi = satu task.** Jangan pernah lempar dua task sekaligus, dan jangan pernah
bilang "kerjakan semua yang di backlog".

Prompt yang dipakai di Antigravity / agent mana pun:

```
Baca AGENTS.md dan MIGRATION-BACKLOG.md.

Kerjakan HANYA <TX>. Jangan menyentuh file di luar yang disebut di task itu.

Sebelum menulis kode: tunjukkan rencana maksimal 6 baris, lalu BERHENTI dan tunggu "gas".

Selesai artinya:
- semua acceptance criteria di task terpenuhi
- pytest -q hijau
- bash scripts/check_invariants.sh tidak menambah pelanggaran
- laporan: apa yang diubah, test apa yang nambah, apa yang BELUM

Kalau ada yang ambigu: pakai format BLOCKED, jangan improvisasi.
```

Kenapa harus berhenti sebelum ngoding: drift terjadi di 5 menit pertama, saat agent
menginterpretasi task. Review rencananya, baru kasih "gas". Ini yang bikin hasilnya beda.

## Urutan & dependency

```
T1 ──► T2          (data jujur dulu)
T3 ──► T4 ──► T5
         └──► T6
T7 (butuh T3+T4)
T8 ──► T9
T10 (butuh T4)
T11 (butuh T10)
T12 (terakhir)
```

T2 dan T3 boleh paralel (file berbeda). Sisanya berurutan.

---

## T1 — Dokumentasikan kebohongan data sebagai test merah

**Goal:** bikin bug-nya kelihatan sebelum diperbaiki. Tanpa ini, T2 nggak bisa dibuktikan.

**Files:** `tests/test_ingest_fabrication.py` (baru)

**Acceptance:**
- Dua test dengan `@pytest.mark.xfail(strict=True, reason="T2 belum dikerjakan")`.
- Test 1: CSV satu periode (semua transaksi di 7 hari terakhir) → assert hasilnya
  `status == "INSUFFICIENT_DATA"`, bukan `qty_change_pct == -50.0`.
- Test 2: CSV tanpa kolom harga → assert `INSUFFICIENT_DATA` dengan alasan menyebut kolom
  yang hilang, bukan `unit_price == 20000`.
- Test-nya deskriptif: nama fungsi menjelaskan perilaku yang **seharusnya**, bukan yang sekarang.

**Verify:** `pytest -q tests/test_ingest_fabrication.py` → `2 xfailed`.

**Size:** S

---

## T2 — Hapus angka karangan dari ingest

**Goal:** `IngestPosDataTool` berhenti mengarang baseline, harga, dan timestamp.

**Files:** `app/services/fnb_marketing/tools.py`, `app/services/fnb_marketing/schemas.py`,
`tests/test_ingest_fabrication.py`

**Acceptance:**
- `PosDataSummary` dapat field `status` (`"OK"` / `"INSUFFICIENT_DATA"`) dan `reason: str | None`.
- Hapus total blok `all_single_period` (yang bikin `base_q = curr_q * 2` dan `-50.0`).
- Hapus default `unit_price = 20000` dan `margin_pct = 62.5`.
- Hapus timestamp hardcoded `2026-10-13 14:00:00` di `parse_pos_file` — pakai waktu dari input,
  atau tandai `INSUFFICIENT_DATA` kalau memang nggak ada.
- `DetectOpportunitiesTool` menolak input `status != "OK"` dengan alasan jelas (bukan exception
  tanpa pesan).
- Dua test T1: hapus marker xfail → hijau.

**Verify:** `pytest -q tests/test_ingest_fabrication.py tests/test_fnb_workflow.py`

**Size:** M

---

## T3 — Satu registry, dan schema di-generate

**Goal:** hapus sistem tool kedua. Schema function-calling lahir dari `BaseTool`.

**Files:** `app/core/tool_registry.py`, `app/services/deepseek_agent.py`,
`tests/test_single_tool_registry.py` (baru)

**Acceptance:**
- `BaseTool` (atau helper di `tool_registry.py`) punya `to_openai_tool()` yang menghasilkan
  `{"type":"function","function":{"name": metadata.tool_type, "description": ..., "parameters": input_schema.model_json_schema()}}`.
- `AGENT_TOOLS` **dihapus** dari `deepseek_agent.py`.
- `execute_tool()` if/elif **dihapus**; diganti lookup `ToolRegistry.get(name).execute(...)`.
- `ToolRegistry.get` pakai `packaging.version.Version` untuk pilih versi terbaru.
- Nama tool memakai `tool_type` (`fnb.plan_campaign`), bukan nama fungsi lama.

**Test:** capture payload yang dikirim ke LLM (fake client), assert himpunan nama tool ==
himpunan `tool_type` di registry. Plus test `1.9.0 < 1.10.0`.

**Verify:** `pytest -q tests/test_single_tool_registry.py`
`grep -rnE "AGENT_TOOLS|elif tool_name ==" app/` → kosong.

**Size:** M

---

## T4 — Agent loop dengan guard

**Goal:** satu fungsi loop yang bisa dipakai chat, telegram, dan assistant.

**Files:** `app/services/agent_loop.py` (baru), `tests/test_agent_loop.py` (baru)

**Acceptance:**
- `run_agent(goal, allowed_tools, max_steps=12, session=None) -> AgentResult`.
- `AgentResult.status in {DONE, MAX_STEPS, TOOL_ERROR, APPROVAL_REQUIRED, LLM_UNAVAILABLE}`.
- Tiap iterasi: LLM → kalau `tool_calls` kosong → `DONE`. Kalau ada → validasi argumen → eksekusi
  → append `role: "tool"` → ulang.
- Error tool **tidak** crash: dikembalikan ke model sebagai `{"error": "..."}` supaya bisa
  self-correct.
- Output tool di-truncate (mis. 8000 char).
- `MAX_TOOL_CALLS_PER_STEP` (mis. 8) untuk mencegah satu giliran memanggil 50 tool.
- Nggak ada `sleep`, nggak ada retry tanpa batas.

**Test (pakai fake client, tanpa network):**
1. LLM minta 1 tool di langkah 1, jawaban final di langkah 2 → `DONE`, tool dipanggil sekali.
2. LLM minta tool terus sampai 12 langkah → `MAX_STEPS`, total panggilan == 12.
3. Tool melempar exception → loop lanjut, hasil error masuk messages, status akhir `DONE`/`TOOL_ERROR`.

**Verify:** `pytest -q tests/test_agent_loop.py`

**Size:** M

---

## T5 — Workflow jadi tool, bukan atasan

**Goal:** agent bisa memutuskan "ini butuh alur retail lengkap" lalu memanggilnya.

**Files:** `app/services/workflow_tool.py` (baru), registrasi di `app/main.py`,
`tests/test_workflow_tool.py` (baru)

**Acceptance:**
- `tool_type = "workflow.run"`, `category = ANALYSIS`, `risk_tier = INTERNAL`.
- Input: `{workflow_id: str, version: int | None, csv_content: str | None}`.
- Wajib ada versi ACTIVE; kalau tidak ada → status `NO_ACTIVE_VERSION` (bukan exception).
- `execute()` memanggil `WorkflowRunner.run_live()` dan mengembalikan ringkasan:
  `{status, nodes_executed, final_output, pending_action}`.
- Kalau runner minta approval → hasilnya `APPROVAL_REQUIRED`, diteruskan ke loop (lihat T6).

**Test:** fake repository dengan satu workflow ACTIVE; panggil tool; assert node dijalankan
dan ringkasan benar. Plus kasus tanpa versi ACTIVE.

**Verify:** `pytest -q tests/test_workflow_tool.py`

**Size:** M

---

## T6 — Approval pindah ke loop

**Goal:** gate berlaku per tool call, bukan per node graph.

**Files:** `app/services/agent_loop.py`, `tests/test_loop_approval.py` (baru)

**Acceptance:**
- Sebelum eksekusi, kalau `tool.metadata.risk_tier in {EXTERNAL, FINANCIAL}` dan
  `context.extra_meta.get("action_approved") is not True`:
  **stop sebelum memanggil tool**, return `APPROVAL_REQUIRED` + payload yang akan dieksekusi.
- `resume_with_approval(result, approved=True)` melanjutkan dari tool yang sama, **tidak**
  mengulang langkah sebelumnya.
- Kalau ditolak: status `REJECTED`, tool tidak pernah dipanggil.

**Test:** fake tool `publish` dengan spy. Skenario: (a) tanpa approval → spy == 0 panggilan,
status `APPROVAL_REQUIRED`; (b) approve → spy == 1; (c) reject → spy == 0.

**Verify:** `pytest -q tests/test_loop_approval.py`

**Size:** M

---

## T7 — Ganti if/elif synthesizer dengan planner LLM

**Goal:** "AI yang bikin workflow" benar-benar AI, atau jujur bilang nggak bisa.

**Files:** `app/services/workflow_synthesizer.py`, `tests/test_synthesizer_planner.py` (baru)

**Acceptance:**
- Hapus cabang `is_inventory` / `is_marketing` / `is_retail_pricing` beserta DAG literalnya.
- `synthesize_from_problem` mengisi `custom_candidate_generator` dengan pemanggil LLM yang
  prompt-nya berisi `get_registry_catalog_specs()`.
- Output LLM wajib lewat `WorkflowValidator` (yang sudah ada). Repair loop maksimal 3x.
- Kalau LLM gagal 3x / provider mati → `is_supported=False`, `rejection_reason="LLM_UNAVAILABLE"`.
  **Dilarang** jatuh ke DAG hardcoded.
- Prompt LLM melarang mengarang `tool_type` di luar katalog.

**Test:**
1. Fake LLM mengembalikan DAG valid → `is_supported=True`, `workflow` lolos validator.
2. Fake LLM mengembalikan JSON rusak 3x → `is_supported=False` dengan alasan LLM, bukan DAG.
3. Fake LLM mengembalikan `tool_type` fiktif → ditolak validator, bukan dipaksa lolos.

**Verify:** `pytest -q tests/test_synthesizer_planner.py`

**Size:** L

---

## T8 — Checkpoint & resume yang idempoten

**Goal:** approve di tengah run nggak menjalankan ulang node yang sudah selesai.

**Files:** `app/services/workflow_runner.py`, `app/services/workflow_repository.py`,
`app/models/workflow.py`, `tests/test_runner_resume.py` (baru)

**Acceptance:**
- Setelah tiap node sukses, output-nya dipersist (`node_outputs` per `run_id`).
- `run_live(run_id=...)` memuat checkpoint dan **melewati** node yang `COMPLETED`.
- Node yang `WAITING_APPROVAL` dijalankan sekali, setelah approve.
- Nggak ada perubahan perilaku kalau `run_id` baru.

**Test:** fake tool `ingest` dengan counter. Run → berhenti di gate → approve → resume →
`counter == 1` (bukan 2). Plus test bahwa output node sebelumnya dipakai ulang (bukan dihitung).

**Verify:** `pytest -q tests/test_runner_resume.py`

**Size:** L

---

## T9 — Hapus in-memory store, semua lewat repository

**Goal:** restart server nggak menghilangkan workflow, run, dan proposal.

**Files:** `app/api/v1/workflows.py`, `app/api/v1/assistant.py`,
`app/services/business_config_service.py`, `tests/test_persistence_restart.py` (baru)

**Acceptance:**
- `_WORKFLOWS_STORE`, `_RUNS_STORE`, `_PROPOSALS_STORE`, `_PENDING_ACTIONS_STORE`,
  `BusinessConfigService._PROPOSALS` **dihapus**.
- Semua baca/tulis lewat `WorkflowRepository`; `try/except: pass` di sekitar persistensi diganti
  `logger.error` + naikkan error ke caller.
- Proposal bisnis dipersist ke tabel (model `WorkflowProposal` sudah ada).

**Test:** buat proposal → tutup session → buka session baru → proposal masih ada. Run detail
masih ada setelah "restart" (session baru).

**Verify:** `grep -rnE "_(WORKFLOWS|RUNS|PROPOSALS|PENDING_ACTIONS)_STORE" app/` → kosong.
`pytest -q tests/test_persistence_restart.py`

**Size:** L

---

## T10 — Memory percakapan yang benar

**Goal:** berhenti menambal gejala dengan prefix dan pemotongan.

**Files:** `app/services/deepseek_agent.py`, `tests/test_conversation_roundtrip.py` (baru)

**Acceptance:**
- Riwayat direkonstruksi lengkap: `user` → `assistant` (+ `tool_calls`) → `tool` (dengan
  `tool_call_id` dan output asli).
- Hapus prefix `[Pertanyaan Sesi Sebelumnya...]`, hapus potongan `[:150]`, hapus penggantian
  frasa manual.
- Compaction berbasis budget token: pertahankan turn terakhir utuh; yang lama diringkas oleh
  LLM (bukan dipotong tengah kalimat).
- Tool output disimpan terpisah dari teks assistant.

**Test:** turn 1 memanggil tool → turn 2 mengirim payload; assert ada message `role: "tool"`
berisi output turn 1 dengan `tool_call_id` yang cocok.

**Verify:** `pytest -q tests/test_conversation_roundtrip.py`

**Size:** M

---

## T11 — Bunuh fallback yang berpura-pura

**Goal:** kalau LLM mati, sistem bilang mati.

**Files:** `app/services/deepseek_agent.py`, `app/services/proposal_generator.py`,
`app/api/v1/agent.py`, test terkait

**Acceptance:**
- `_fallback_local_agent()` dan `_rule_based_patch_parser()` **dihapus**.
- Tanpa provider: endpoint balas `503` dengan body `{"code": "LLM_UNAVAILABLE"}`.
- Test lama yang mengandalkan fallback dihapus — bukan di-skip.
- README diperbarui: hapus klaim "Intelligent Fallback Agent Engine".

**Test:** monkeypatch provider kosong → assert 503 + code. Assert fungsi lama nggak ada lagi
(`hasattr` False / grep).

**Verify:** `grep -rnE "_fallback_local_agent|_rule_based_patch_parser" app/` → kosong.

**Size:** S

---

## T12 — Pasang gate-nya di CI

**Goal:** aturan yang nggak dicek otomatis akan dilanggar lagi bulan depan.

**Files:** `scripts/check_invariants.sh`, `.github/workflows/ci.yml`, `README.md`

**Acceptance:**
- Script ada dan **exit 0** di kondisi bersih.
- CI menjalankan `pytest -q` + script untuk setiap PR.
- README punya bagian "Agent contribution rules" yang menunjuk ke `AGENTS.md`.
- `AGENTS.md` lama di `.agent/skills/ai-engineer/SKIIL.md` dipindah ke `docs/arsip/`.

**Verify:** `bash scripts/check_invariants.sh; echo $?` → `0`

**Size:** S

---

## Jangan dikerjakan dulu

Sampai T4 hijau, ini semua **dilarang** — bukan karena jelek, tapi karena menambah drift:

- Ubah UI canvas / `kirata_builder.html`
- Tambah template atau tool pack baru
- Tambah model/LLM provider baru
- Rapikan style, rename, refactor
- Ubah prompt biar "lebih pintar" (itu bukan tempat masalahnya)

Kalau keinginan itu muncul saat ngerjain task: catat di bawah, jangan dikerjakan.

## Catatan progres

Isi manual. Angka pelanggaran diambil dari `bash scripts/check_invariants.sh`.

| Task | Status | Pelanggaran | Tanggal | Catatan |
|---|---|---|---|---|
| baseline | — | 70 baris (6 grup) | 2026-11-17 | baseline awal sebelum migrasi |
| T1 | ☑ | 70 baris (6 grup) | 2026-11-17 | 3 xfailed di tests/test_ingest_fabrication.py |
| T2 | ☑ | 65 baris (6 grup) | 2026-11-17 | turun 5 baris I3; 4 test ingest_fabrication hijau |
| T3 | ☑ | 47 baris (3 grup) | 2026-11-17 | I1, I2, I10 hijau; 3 test single_tool_registry hijau |
| T4 | ☑ | 47 baris (3 grup) | 2026-11-17 | agent_loop.py + guard I5; 4 test agent_loop hijau |
| T5 | ☑ | 47 baris (3 grup) | 2026-11-17 | workflow_tool.py + registrasi di main.py; 4 test hijau |
| T6 | ☑ | 47 baris (3 grup) | 2026-11-17 | approval gate I6 di agent_loop + resume_with_approval; 2 test hijau |
| T7 | ☑ | 47 baris (3 grup) | 2026-11-17 | synthesizer pakai planner LLM tanpa if/elif; 4 test planner hijau |
| T8 | ☑ | 47 baris (3 grup) | 2026-11-17 | NodeCheckpoint + resume idempoten di runner; 2 test hijau |
| T9 | ☑ | 10 baris (2 grup) | 2026-11-17 | I4 hijau (turun 37 baris); semua lewat WorkflowRepository; 2 test hijau |
| T10 | ☑ | 10 baris (2 grup) | 2026-11-17 | rekonstruksi user->assistant(+tool_calls)->tool + compaction; 2 test hijau |
| T11 | ☑ | 5 baris (1 grup) | 2026-11-17 | I7 hijau; _fallback_local_agent & _rule_based_patch_parser dihapus; 503 LLM_UNAVAILABLE |
| T12 | ☑ | 0 baris (0 grup) | 2026-11-17 | check_invariants.sh exit 0; CI workflow + README rules dipasang |
