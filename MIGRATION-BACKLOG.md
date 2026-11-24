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

## FASE 2 — baru dibuka setelah T1–T12 terbukti di remote

**Gerbang:** jangan sentuh bagian ini sebelum ini semua benar di `origin/main`:

```bash
git log -1 --oneline          # harus ada commit setelah T12
git ls-tree -r origin/main --name-only | grep -E "agent_loop|workflow_tool|check_invariants"
```

Kalau `agent_loop.py` belum ada di remote, T1–T12 belum selesai — sekeras apa pun laporannya.

---

## T13 — Adapter nama tool yang aman untuk provider

**Goal:** nama fungsi yang dikirim ke LLM lolos `^[a-zA-Z0-9_-]{1,64}$`.

**Latar:** DeepSeek/OpenAI menolak `analytics.get_sales_overview` (titik nggak diizinkan).
Balasannya `400 Invalid 'tools[0].function.name'`. Ini nggak ketahuan di test karena klien
palsu nggak menegakkan aturan provider.

**Files:** `app/core/tool_registry.py`, `app/services/agent_loop.py`,
`tests/test_tool_name_contract.py` (baru)

**Acceptance:**
- `to_wire_name(tool_type)` mengganti `.` → `__`; `from_wire_name(nama)` mengembalikannya.
- Round-trip untuk **semua** tool di registry: `from_wire_name(to_wire_name(t)) == t`.
- Semua wire name lolos regex dan panjangnya ≤64.
- Tabrakan nama (`a.b__c` vs `a__b.c`) ditolak saat register, bukan saat runtime.
- Lookup tool call memakai decode, bukan pencocokan string manual.

**Test:** parametrize seluruh registry → assert regex + round-trip. Plus satu test yang
memvalidasi **payload asli** yang dikirim ke fake client (bukan cuma helper-nya).

**Verify:** `pytest -q tests/test_tool_name_contract.py`

**Size:** S

---

## T14 — Sambungkan chat ke loop, lalu hapus jalur lama

**Goal:** satu jalur percakapan. Bukan dua.

**Files:** `app/services/deepseek_agent.py`, `app/api/v1/agent.py`,
`app/bot/telegram_bot.py`, test terkait

**Acceptance:**
- `DeepSeekAgentService.chat()` memanggil `run_agent()`. Blok "panggilan LLM 1 + panggilan
  LLM 2" **dihapus**, bukan dibiarkan sebagai cabang.
- Kalau loop balas `APPROVAL_REQUIRED`: endpoint balas `202` + `action_id`
  (bukan `500`, bukan jawaban palsu).
- Telegram memakai jalur yang sama, bukan salinan logika sendiri.
- Helper string di `execute_tool` (`"." not in tool_name` → `f"analytics.{tool_name}"`)
  dihapus, karena chat nggak lagi manggil tool di luar loop.
- Test: fake LLM skenario 3 langkah → assert tool dipanggil lebih dari sekali
  (bukti multi-step, bukan 2 panggilan tetap).

**Verify:** `grep -n "run_agent" app/services/deepseek_agent.py` → ada.
`pytest -q tests/test_agent.py tests/test_api.py`

**Size:** M

**Kenapa ini penting:** tanpa T14, `agent_loop` cuma komponen yang ada di repo tapi nggak
dipakai. Itu penyakit "dua sistem" yang sama, cuma naik satu level.

**Catatan keamanan:** `chat()` sekarang mengirim SEMUA tool (`get_all_latest()`), termasuk
`fnb.publish_instagram` (EXTERNAL), sementara gate approval cuma ada di `agent_loop`.
Jadi jalur chat bisa mengeksekusi tool berside-effect tanpa approval. T14 menutup ini —
tanpa kode tambahan, karena loop sudah punya gate-nya.

---

## T15 — Smoke test live + config model

**Goal:** bukti end-to-end dengan API nyata, bukan fake.

**Files:** `scripts/smoke_live.py` (baru), `.env.example`, `docs/progress/`

**Acceptance:**
- Script mengirim satu pertanyaan nyata, minimal satu tool call tereksekusi, dan menyimpan
  transkrip ke `docs/progress/live-smoke-<YYYY-MM-DD>.md`.
- Kalau API key nggak ada: keluar dengan pesan jelas (`skip: ATMOROUTER_API_KEY kosong`),
  bukan gagal diam-diam.
- Kalau model 404: pesan error menyebut variabel `.env` mana yang harus diubah.
- `.env.example` memakai nama model yang benar-benar valid.

**Verify:** `python scripts/smoke_live.py` → transkrip tersimpan.

**Size:** S

---

## FASE 3 — dari "agent yang jalan" ke "agent yang dipercaya"

**Gerbang:** T13–T15 sudah ada di `origin/main` (sudah terbukti per 5955d66).

Urutan eksekusi: **T18 → T16 → T17 → T19 → T20 → T21**.

---

## T16 — Eval harness: ukur perilaku model, bukan cuma kode

**Goal:** punya angka yang bisa dibandingkan tiap kali prompt/model/tool berubah.

**Files:** `evals/golden_set.jsonl` (baru), `evals/run_eval.py` (baru),
`evals/README.md`, `tests/test_eval_harness.py` (baru)

**Acceptance:**
- Minimal **40 kasus**, tiap baris JSON punya field:
  `id`, `category`, `pertanyaan`, `expected_tools` (list), `forbidden_tools` (list),
  `expected_facts` (dict, opsional), `notes`.
- Kategori wajib ada semua: `analytics`, `inventory`, `pricing`, `workflow`,
  `ambiguity` (harus tanya balik, bukan ngarang), `safety` (harus berhenti di approval).
- Sumber kasus: 30 dari log chat nyata yang sudah ada, 10 kasus negatif buatan lo sendiri.
- `python evals/run_eval.py --offline` jalan tanpa network (pakai fake LLM + fixture DB).
- `--live` memakai provider asli dan menyimpan transkrip.
- Output: `evals/reports/<YYYY-MM-DD>-<sha>.json` berisi
  `tool_selection_accuracy`, `forbidden_violation_rate`, `fact_match_rate`,
  `status_distribution`, `avg_latency_ms`, `avg_cost_idr`.
- Exit code 1 kalau `forbidden_violation_rate > 0` atau `tool_selection_accuracy` di bawah
  threshold yang bisa diatur (`--min-accuracy`, default 0.8).
- `test_eval_harness.py` cuma memvalidasi **schema** golden set (field wajib, kategori valid,
  id unik, minimal 40 kasus) — tanpa network.

**Verify:** `python evals/run_eval.py --offline; echo $?` → `0`

**Size:** L

---

## T17 — Trace & biaya per run

**Goal:** bisa jawab "kenapa jawabannya salah" dan "berapa duit per percakapan".

**Files:** `app/models/agent_run.py` (baru), `app/services/agent_trace.py` (baru),
`app/services/deepseek_agent.py`, migration, `tests/test_agent_trace.py` (baru)

**Acceptance:**
- Tabel `agent_runs`: `run_id`, `conversation_id`, `outlet_id`, `status`, `provider`, `model`,
  `steps_taken`, `tool_calls_count`, `latency_ms`, `prompt_tokens`, `completion_tokens`,
  `est_cost_idr`, `trigger` (`chat` / `schedule` / `telegram`), `created_at`.
- Tabel `agent_steps`: `run_id`, `step`, `tool_name`, `arguments_json` (dipotong 2k),
  `output_digest` (dipotong 2k), `error`, `duration_ms`.
- Satu `chat()` = tepat satu `agent_runs` + N `agent_steps`.
- Kalau provider tidak mengirim `usage`, field token diisi `NULL` — **bukan** 0 atau angka karangan.
- `GET /api/v1/agent/runs/{run_id}` mengembalikan run + steps-nya (read-only).

**Test:** fake LLM 2 langkah → 1 run + 2 step; session baru (simulasi restart) → masih ada;
provider tanpa usage → token `NULL`.

**Verify:** `pytest -q tests/test_agent_trace.py`

**Size:** M

---

## T18 — Approval yang bisa dilanjutkan

**Goal:** tutup lubang "gate bisa berhenti tapi nggak bisa lanjut".

**Files:** `app/models/agent_run.py` (tabel `pending_approvals`), `app/api/v1/agent.py`,
`app/services/deepseek_agent.py`, `app/bot/telegram_bot.py`,
`tests/test_approval_roundtrip.py` (baru)

**Acceptance:**
- Saat `APPROVAL_REQUIRED`, server menyimpan `pending_approvals`:
  `action_id` (uuid4), `run_id`, `tool_name`, `arguments`, `risk_tier`, `status=PENDING`,
  `messages_json`, `traces_json` (cukup untuk melanjutkan loop), `expires_at` (+24 jam).
- `POST /api/v1/agent/approvals/{action_id}/approve` → load pending, panggil
  `resume_with_approval(..., approved=True)`, simpan hasil run, balas jawaban final.
- `POST /api/v1/agent/approvals/{action_id}/reject` → status `REJECTED`, tool **tidak pernah**
  dieksekusi.
- Approve dua kali → `409`, dan tool tetap dieksekusi **tepat satu kali**.
- Pending lewat `expires_at` → `410`, tidak bisa di-approve.
- `reviewed_by` tercatat di record.
- Telegram: tampilkan `action_id` + instruksi cara approve.

**Test:** (a) approve sekali → tool jalan 1x; (b) approve dua kali → 409 dan tetap 1x;
(c) reject → 0x; (d) expired → 410; (e) dua approval berbeda jalan independen.

**Verify:** `pytest -q tests/test_approval_roundtrip.py`

**Size:** M

---

## T19 — Tool pertama yang benar-benar menyentuh dunia nyata

**Goal:** loop lo belum pernah punya efek nyata. Mulai dari satu, dengan idempotensi.

**Files:** `app/services/whatsapp_tool.py` (baru), `.env.example`,
`tests/test_whatsapp_tool.py` (baru)

**Acceptance:**
- `tool_type = "whatsapp.send_message"`, `risk_tier = EXTERNAL`,
  `input_schema` wajib punya `idempotency_key: str`.
- `WHATSAPP_SANDBOX=true` (default) → tidak ada network call; pesan dicatat ke tabel outbox.
- Tanpa kredensial → status `NOT_CONFIGURED` (bukan exception, bukan diam-diam sukses).
- Kirim ulang dengan `idempotency_key` sama → tidak ada pengiriman kedua, balikan hasil pertama.
- `ToolRegistry.register()` menolak tool EXTERNAL yang tidak punya `idempotency_key`
  (lihat I19) — plus test-nya.

**Test:** fake client: (a) kirim 1x; (b) kirim ulang key sama → 1x; (c) sandbox → 0 network,
1 outbox; (d) tanpa kredensial → `NOT_CONFIGURED`; (e) register tool EXTERNAL tanpa
`idempotency_key` → `ValueError`.

**Verify:** `pytest -q tests/test_whatsapp_tool.py tests/test_idempotency_contract.py`

**Size:** M

---

## T20 — Memory bisnis yang tahan lama

**Goal:** agent "kenal" tokonya, bukan cuma inget chat kemarin.

**Files:** `app/models/business_memory.py` (baru), `app/services/memory_service.py` (baru),
`app/services/deepseek_agent.py`, migration, `tests/test_business_memory.py` (baru)

**Acceptance:**
- Tabel `business_facts`: `outlet_id`, `key`, `value`, `confidence`, `source`, `updated_at`.
- Key yang didukung (allowlist): `menu_andalan`, `margin_target_pct`, `supplier_utama`,
  `jam_ramai`, `jam_sepi`, `budget_promo_idr`, `target_harian_idr`.
- `get_facts(outlet_id)` diinjeksi ke system prompt sebagai blok `KONTEKS BISNIS`,
  **maksimal 500 token**; kalau lewat, potong berdasarkan `updated_at` terbaru.
- Tool `memory.remember(key, value)` (`risk_tier=INTERNAL`) untuk menyimpan fakta baru.
- Key di luar allowlist ditolak dengan alasan jelas.
- Dilarang menyimpan PII pelanggan (nomor HP, nama pembeli). Ada test yang menolak.

**Test:** simpan fakta → chat berikutnya memuat blok konteks; isi 30 fakta → blok tetap ≤500
token; key terlarang ditolak; PII ditolak.

**Verify:** `pytest -q tests/test_business_memory.py`

**Size:** M

---

## T21 — Agent yang inisiatif: scheduler → run_agent

**Goal:** berhenti jadi chatbot yang harus ditanya.

**Files:** `app/services/proactive.py` (baru), `app/main.py`, migration (kalau perlu),
`tests/test_proactive.py` (baru)

**Acceptance:**
- Job harian pada jam yang bisa diatur (default 07:00 dan 21:00 WIB) memanggil `run_agent`
  dengan goal tetap (contoh: "ringkas performa hari ini + 1 saran aksi").
- Hasil dikirim ke Telegram/WhatsApp dan dicatat sebagai `agent_runs` dengan `trigger="schedule"`.
- Idempoten per slot: menjalankan job dua kali untuk tanggal+slot yang sama → tetap 1 run,
  1 pesan.
- Kalau LLM mati: catat run `FAILED` + alasan, scheduler **tidak** crash, tidak retry tanpa batas.
- Jam dan tenant dibaca dari config, bukan hardcoded.

**Test:** fake clock + fake channel: (a) satu slot → 1 run + 1 pesan; (b) dua kali slot sama →
tetap 1; (c) provider down → run `FAILED`, tidak ada exception yang lolos dari job.

**Verify:** `pytest -q tests/test_proactive.py`

**Size:** M

---

## FASE 4 — Content Studio & Instagram Publishing

**Gerbang:** T21 selesai + eval awal sudah dijalankan dan angkanya dilaporkan.

Urutan eksekusi: **T22 → T23 → T24 → T25 → T26 → T27**

---

## T22 — Content Studio: 3 varian konten dari satu kampanye

**Goal:** satu kampanye menghasilkan beberapa pilihan konten, bukan satu caption template.

**Files:** `app/services/content_studio.py` (baru), `app/models/campaign.py` (tambah
`CampaignContent`), `tests/test_content_studio.py` (baru), `tests/test_content_grounding.py` (baru)

**Acceptance:**
- Input: payload kampanye (`plan_campaign` output) → output: **3 varian** dengan gaya berbeda
  (`hard_sell`, `soft_relatable`, `humor`).
- Tiap varian: `caption`, `hashtags` (3–5), `cta`, `image_prompt`, `variant_index`.
- Caption Bahasa Indonesia, ≤2200 karakter, tanpa emoji berlebihan (maks 3).
- **Grounding (I20):** validator mengekstrak semua angka dari caption; setiap angka harus
  ada di payload. Kalau ada angka asing → varian ditolak, bukan dipakai.
- Nama produk tidak boleh muncul kalau tidak ada di payload.
- Disimpan sebagai `CampaignContent(status="DRAFT")`, bukan langsung tayang.

**Test:** (a) LLM mengarang "diskon 50%" padahal payload 15% → ditolak; (b) LLM menyebut
produk yang tidak ada → ditolak; (c) 3 varian valid → tersimpan sebagai DRAFT; (d) caption
>2200 char → ditolak.

**Verify:** `pytest -q tests/test_content_studio.py tests/test_content_grounding.py`

**Size:** M

---

## T23 — Asset pipeline: SVG → JPEG → URL publik

**Goal:** flyer yang sekarang SVG harus jadi JPEG yang bisa diambil Instagram.

**Files:** `app/services/asset_renderer.py` (baru), storage adapter, `.env.example`,
`tests/test_asset_renderer.py` (baru)

**Acceptance:**
- SVG 1080×1080 → JPEG 1080×1080, kualitas 90, ukuran ≤8MB.
- Validasi sebelum upload: rasio 1:1 (atau 4:5), tipe `image/jpeg`, ukuran ≤8MB.
- Upload ke storage, balikan `public_url`; verifikasi `HEAD` mengembalikan 200 +
  `content-type: image/jpeg`.
- Kalau storage belum dikonfigurasi → status `STORAGE_NOT_CONFIGURED`, bukan exception.
- Sandbox: tetap render + simpan lokal, `public_url=None`.

**Test:** render fixture SVG → assert JPEG, dimensi, ukuran; SVG rusak → error jelas;
tanpa konfigurasi → `STORAGE_NOT_CONFIGURED`.

**Verify:** `pytest -q tests/test_asset_renderer.py`

**Size:** M

---

## T24 — `instagram.publish` (satu jalur, bukan dua)

**Goal:** publish sungguhan, idempoten, dengan rate limit.

**Files:** `app/services/instagram_api.py` (baru), `app/services/instagram_publisher.py`
(refactor), `app/services/fnb_marketing/tools.py` (**hapus** `publish_instagram` yang lama),
`tests/test_instagram_publish.py` (baru)

**Acceptance:**
- **Hapus** `fnb.publish_instagram` (mock lama). Satu jalur publish saja: `instagram.publish`.
- Input: `content_id` (wajib), `idempotency_key` (wajib). **Tidak ada** field caption/teks.
- Alur: load `CampaignContent` → harus `APPROVED` (DRAFT → 409) → render asset → create media
  container → tunggu status `FINISHED` (polling, timeout 60s) → `media_publish` → simpan `post_id`.
- Idempoten (I22): `content_id` yang sudah tayang → balikan post lama, jangan panggil API.
- Rate limit (I22): hitung publish 24 jam; ≥25 → `RATE_LIMITED`, tanpa panggil API.
- Error taxonomy eksplisit: `TOKEN_EXPIRED`, `MEDIA_INVALID`, `RATE_LIMITED`, `API_ERROR` —
  masing-masing pesan yang bisa ditindaklanjuti, tanpa crash.
- `INSTAGRAM_SANDBOX=true` (default): tidak memanggil API, status `SANDBOX_PUBLISHED` dengan
  label jelas. **Dilarang** mengembalikan status `PUBLISHED` saat sandbox.
- `risk_tier=EXTERNAL` → otomatis kena approval gate di `agent_loop`.

**Test:** (a) content DRAFT → 409; (b) publish sukses → 1 post; (c) publish ulang content
sama → tidak ada panggilan API kedua; (d) 25 publish → yang ke-26 `RATE_LIMITED`; (e) sandbox
→ 0 network, status `SANDBOX_PUBLISHED`; (f) token invalid → `TOKEN_EXPIRED`.

**Verify:** `pytest -q tests/test_instagram_publish.py`

**Size:** L

---

## T25 — Approval konten (bukan cuma approval tool)

**Goal:** owner memilih varian mana yang tayang, sebelum publish dieksekusi.

**Files:** `app/api/v1/campaigns.py`, `app/services/content_studio.py`,
`tests/test_content_approval.py` (baru)

**Acceptance:**
- `POST /campaigns/{campaign_id}/contents/{content_id}/approve` → status `APPROVED`,
  catat `approved_by` + waktu.
- Meng-approve varian kedua **otomatis** membatalkan approval varian pertama
  (satu kampanye = satu konten tayang, kecuali carousel).
- Konten `REJECTED` tidak bisa di-approve ulang tanpa dibuat ulang.
- Publish konten yang belum `APPROVED` → 409 (lihat T24).

**Test:** approve A → publish A ok; approve B → A otomatis `SUPERSEDED`; publish A → 409.

**Verify:** `pytest -q tests/test_content_approval.py`

**Size:** S

---

## T26 — Jam tayang dari data peak hours

**Goal:** ini pembeda produk lo — waktu posting nggak ditebak, tapi dihitung dari data kasir.

**Files:** `app/services/posting_scheduler.py` (baru), `app/main.py`,
`tests/test_posting_scheduler.py` (baru)

**Acceptance:**
- Rekomendasi slot = 1 jam sebelum jam ramai teratas dari `analytics.get_peak_hours`
  (mis. peak 14:00 → slot 13:00).
- Kalau data peak kosong → rekomendasi `DEFAULT_SLOT` (mis. 11:00) dengan alasan jelas,
  bukan angka karangan.
- Scheduler memicu publish pada slot **hanya jika** konten sudah `APPROVED`.
- Idempoten per (content_id, tanggal) — satu konten tayang sekali.
- Rekomendasi disimpan dan disebutkan ke owner saat approval ("tayang jam 13:00, 1 jam
  sebelum jam ramai").

**Test:** peak 14:00 → slot 13:00; data kosong → default + alasan; dua kali slot sama → 1 publish.

**Verify:** `pytest -q tests/test_posting_scheduler.py`

**Size:** M

---

## T27 — Ukur hasilnya (feedback loop)

**Goal:** tanpa ini, "konten berhasil" cuma perasaan.

**Files:** `app/services/instagram_insights.py` (baru), model `CampaignPerformance`,
`tests/test_instagram_insights.py` (baru)

**Acceptance:**
- Ambil metrik 24 jam dan 72 jam setelah publish: `reach`, `saved`, `profile_visits`,
  `follows` (butuh permission insights; kalau tidak ada → status `INSIGHTS_UNAVAILABLE`,
  bukan error).
- Simpan per post; tampilkan ringkasannya di percakapan berikutnya ("post minggu lalu
  reach 1.2k, 43 simpan").
- Dipakai sebagai input kampanye berikutnya: produk yang kontennya performanya bagus
  diprioritaskan minggu depan.
- Dilarang menampilkan angka kalau datanya `NULL`.

**Test:** fake API → 2 metrik tersimpan; permission kurang → `INSIGHTS_UNAVAILABLE`; data
kosong → tidak ada angka di ringkasan.

**Verify:** `pytest -q tests/test_instagram_insights.py`

**Size:** M

---

## Jangan dikerjakan dulu (Fase 4)

- **Carousel & Reels** — tunggu feed image stabil dulu.
- **Auto-post tanpa approval** — sekali salah tayang di akun bisnis orang, kepercayaan hilang.
- **Multi-akun IG per tenant** — nambah kompleksitas token & rate limit.
- **Story publishing** — tidak didukung API; jangan dijanjikan ke user.
- **Subagent/orchestrator** — belum relevan.

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
| T13 | ☑ | 0 baris (0 grup) | 2026-11-18 | I15 + wire name adapter (`.` -> `__`), 30 test contract hijau |
| T14 | ☑ | 0 baris (0 grup) | 2026-11-18 | chat() & Telegram pakai run_agent(); blok lama & helper string dihapus; 202 + action_id |
| T15 | ☑ | 0 baris (0 grup) | 2026-11-18 | scripts/smoke_live.py + .env.example (alicn/deepseek-v4.1-flash) + live-smoke-2026-11-18.md |
| T16 | ☑ | 0 baris (0 grup) | 2026-11-23 | evals/golden_set.jsonl (42 kasus, 6 kategori) + run_eval.py (--offline/--live) + 2 test eval_harness hijau |
| T17 | ☑ | 0 baris (0 grup) | 2026-11-23 | agent_runs + agent_steps + agent_trace.py + GET /runs/{run_id} + 2 test agent_trace hijau |
| T18 | ☑ | 0 baris (0 grup) | 2026-11-23 | pending_approvals + approve/reject endpoints (409/410) + Telegram + 4 test approval_roundtrip hijau |
| T19 | ☑ | 0 baris (0 grup) | 2026-11-23 | whatsapp.send_message (sandbox/idempotent) + I19 di ToolRegistry.register() + 7 test hijau |
| T20 | ☑ | 0 baris (0 grup) | 2026-11-24 | business_facts + memory.remember + KONTEKS BISNIS <=500 token + anti-PII + 3 test hijau |
| T21 | ☑ | 0 baris (0 grup) | 2026-11-24 | proactive.py + ProactiveSlotRun + trigger='schedule' + 3 test hijau |
| T22 | ☑ | 0 baris (0 grup) | 2026-11-24 | content_studio.py + CampaignContent + grounding I20 + 6 test hijau |
| T23 | ☑ | 0 baris (0 grup) | 2026-11-24 | asset_renderer.py (SVG->JPEG 1080x1080, STORAGE_NOT_CONFIGURED, HEAD 200) + 4 test hijau |
| T24 | ☑ | 0 baris (0 grup) | 2026-11-24 | instagram.publish (hapus fnb.publish_instagram) + I21/I22 + 6 test hijau |
| T25 | ☑ | 0 baris (0 grup) | 2026-11-24 | approve/reject content endpoints + SUPERSEDED + 3 test content_approval hijau |
| T26 | ☑ | 0 baris (0 grup) | 2026-11-24 | posting_scheduler.py + PostingSchedule + peak-1h/DEFAULT_SLOT + 3 test hijau |
| T27 | ☑ | | | insights 24h/72h |
