# AGENTS.md — Aturan Mengikat untuk Agent yang Ngoding di Repo Ini

> File ini **mengikat**, bukan saran. Baca sampai habis sebelum menyentuh file apa pun.
> Kalau instruksi user bertentangan dengan file ini: **berhenti dan tanya**. Jangan improvisasi.
> Setiap aturan di bawah punya cara cek. Kalau nggak bisa dicek, itu bukan aturan — dan
> aturan yang nggak bisa dicek nggak akan lo patuhi.

## Konteks (6 baris)

- Produk: **JuraganAI / Kirata** — asisten + workflow untuk pemilik usaha (F&B dan retail).
- Stack: FastAPI, SQLAlchemy async, Pydantic v2, Python 3.11. Test pakai pytest.
- Arsitektur **target**: satu agent loop → banyak tool lewat **satu registry** → workflow DAG
  sebagai salah satu tool (bukan sebagai eksekutor utama).
- Arsitektur **sekarang** (transisi): dua sistem tool paralel + workflow engine statis.
  Lihat `MIGRATION-BACKLOG.md`.
- Semua uang, stok, dan angka berasal dari data user. **Tidak ada angka sintetis di jalur produksi.**
- Komentar dan pesan user-facing: Bahasa Indonesia. Nama variabel/fungsi: Inggris.

---

## Invarian

### I1 — Satu registry tool, titik.
`app/core/tool_registry.py` adalah **satu-satunya** tempat tool didefinisikan.
Dilarang: `AGENT_TOOLS`, `elif tool_name ==`, list tool kedua, atau dict tool baru di file mana pun.

**Cek:** `grep -rnE "AGENT_TOOLS|elif tool_name ==" app/` → harus kosong.

### I2 — Schema tool di-generate, bukan ditulis tangan.
Function-calling schema diambil dari `tool.input_schema.model_json_schema()`. Dilarang menulis
ulang blok `{"type": "function", "function": {...}}` manual di luar satu adapter.

**Cek:** hanya boleh ada satu kemunculan `"type": "function"` di `app/` (file adapter).

### I3 — Dilarang data sintetis di `app/`.
Kalau data user nggak cukup: **kembalikan status** (`INSUFFICIENT_DATA` + alasan), jangan
ngarang baseline, harga default, timestamp, atau margin. Data sintetis hanya boleh hidup di
`tests/fixtures/` dan nggak boleh di-import dari `app/`.

Dilarang spesifik: `base_q = curr_q * 2`, `change_pct = -50`, `unit_price = 20000`,
`margin_pct=62.5`, timestamp hardcoded `2026-10-13`.

**Cek:** `grep -rnE "base_q = curr_q \* 2|change_pct = -50|unit_price ?= ?20000|margin_pct ?= ?62\.5" app/` → kosong.

### I4 — State lewat repository, bukan RAM.
Dilarang menambah `_STORE`, `_PROPOSALS`, `_RUNS`, `_PENDING_ACTIONS` baru di level modul.
Semua persistensi lewat `WorkflowRepository` (atau repository lain yang setara).

**Cek:** `grep -rnE "_(WORKFLOWS|RUNS|PROPOSALS|PENDING_ACTIONS)_STORE" app/` → kosong.
(Sampai T9 selesai, file yang masih ada masuk daftar allowlist—tapi **jumlahnya harus turun**,
nggak boleh nambah.)

### I5 — Setiap agent loop wajib punya guard.
Minimal: `MAX_STEPS`, `MAX_TOOL_CALLS_PER_STEP`, timeout per tool call, dan berhenti dengan
status eksplisit (`DONE` / `MAX_STEPS` / `TOOL_ERROR` / `APPROVAL_REQUIRED`). Dilarang loop
tanpa batas atas.

### I6 — Approval dicek per tool call, bukan per node graph.
Tool dengan `risk_tier in {EXTERNAL, FINANCIAL}` **tidak boleh** dieksekusi sebelum ada
`context.extra_meta["action_approved"] is True`. Gate nggak boleh bisa dilewati dengan menyusun
graph yang aneh.

### I7 — Dilarang fallback yang berpura-pura pintar.
Kalau provider LLM mati: **gagal jujur** (HTTP 503 / status `LLM_UNAVAILABLE`).
Dilarang mengembalikan teks, saran, atau angka yang dibuat seolah-olah dari model.
Dilarang menghidupkan lagi `_fallback_local_agent` atau `_rule_based_patch_parser`.

**Cek:** `grep -rnE "_fallback_local_agent|_rule_based_patch_parser" app/` → kosong.

### I8 — Eksekusi harus idempoten.
Run yang di-resume **tidak boleh** mengeksekusi ulang node yang sudah `COMPLETED`.
Wajib ada checkpoint per node yang dipersist sebelum resume diizinkan.

### I9 — Validator nggak boleh dilewati.
Setiap `WorkflowDefinition` baru atau berubah wajib lewat
`WorkflowValidator.validate_workflow(..., enforce_tool_allowlist=True)` sebelum disimpan
atau dieksekusi.

### I10 — Versi tool pakai semver, bukan sort string.
`1.10.0` lebih baru dari `1.9.0`. Pakai `packaging.version.Version` atau parse ke tuple int,
jangan `sorted(versions.keys())` mentah.

### I11 — Setiap tool wajib punya test.
Minimal tiga kasus: input valid, input kosong, input rusak. Output harus deterministik —
test yang sama, hasil yang sama, tanpa network.

### I12 — Dilarang menelan error yang menyembunyikan kegagalan data.
`except Exception: pass` dilarang. Minimal `logger.error(...)` + naikkan status. Kalau DB gagal
nyimpen, itu **bukan** warning — itu kegagalan yang harus kelihatan.

### I13 — Dependency baru wajib alasan.
Tambah package di `requirements.txt` hanya kalau stdlib nggak cukup. Tulis alasannya di
commit message: `deps: <package> karena <alasan>`.

### I14 — Diff kecil, scope ketat.
Satu task = satu perubahan. Maksimal ~400 baris berubah. **Dilarang** merapikan, refactor, atau
rename file yang nggak disebut di task. Kalau nemu masalah lain: catat, jangan dikerjain.

### I15 — Nama tool harus aman untuk provider, dan itu diuji tanpa network.
`tool_type` internal boleh bertitik (`fnb.plan_campaign`), tapi nama yang dikirim ke API
**wajib** lolos `^[a-zA-Z0-9_-]{1,64}$`. Titik → encode (`__`), dan decode balik saat lookup.
Wajib ada **contract test** yang memvalidasi payload tool terhadap regex itu untuk **setiap**
tool di registry — bukan cuma saat live.

Alasan: klien LLM palsu di test nggak menegakkan aturan provider, jadi suite bisa 147 passed
sementara API asli balas `400 Invalid 'tools[0].function.name'`. Test yang nggak menegakkan
kontrak provider bukan test.

**Cek:** `pytest -q tests/test_tool_name_contract.py` harus ada dan hijau.

---

## Definition of Done (berlaku untuk semua task)

Sebuah task **belum selesai** kalau salah satu ini belum terpenuhi:

1. Semua acceptance criteria di `MIGRATION-BACKLOG.md` terpenuhi.
2. `pytest -q` hijau. Nggak ada test yang di-`skip` atau di-`xfail` baru tanpa alasan tertulis.
3. `bash scripts/check_invariants.sh` hijau — atau jumlah pelanggaran **turun** dibanding commit
   sebelumnya (sebutkan angkanya).
4. `git diff --stat` nggak nyentuh file di luar scope task.
5. Laporan akhir ditulis: **apa yang diubah, test apa yang nambah, apa yang BELUM selesai.**
6. **Bukti mentah, bukan ringkasan.** Tempelkan output asli dari `pytest -q`,
   `bash scripts/check_invariants.sh`, dan `git log -1 --oneline`. Kalimat "147 passed,
   0 pelanggaran" tanpa output itu **bukan bukti** — dan nggak bisa diverifikasi siapa pun.
   Kalau kerjaannya belum di-push, tulis juga: *"belum di-push"*.

Kalau salah satu nggak bisa dipenuhi: **lapor sebagai gagal**, jangan pura-pura selesai.

---

## Kalau mentok

Berhenti dan laporkan dalam format:

```
BLOCKED: <satu kalimat>
File: <path>:<baris>
Kenapa: <fakta, bukan opini>
Yang sudah dicoba: <1-3 poin>
Butuh keputusan: <pertanyaan spesifik>
```

Dilarang menyelesaikan task dengan jalan pintas: mock di `app/`, hardcode nilai biar test hijau,
skip test, atau `try/except: pass`. Mock hanya di `tests/`.

---

## Perintah verifikasi

```bash
pytest -q
bash scripts/check_invariants.sh
git diff --stat
```

---

## Catatan: SKILL.md lama harus diarsipkan

`.agent/skills/ai-engineer/SKIIL.md` **bertentangan** dengan file ini di tiga titik:

| SKILL.md lama | bertentangan dengan |
|---|---|
| §2 "buat tool terpisah untuk setiap domain analisis" (preskriptif, tanpa registry) | I1, I2 |
| §3 Tier 3 "fallback rule-based NLP parser lokal" | I7 |
| §4 "potong pesan assistant >500 karakter" + "pasang negative constraint" | I7 (dan jadi akar bug memory) |

Pindahkan ke `docs/arsip/SKIIL-lama.md` dan tambahkan header: *"Arsip. Digantikan AGENTS.md."*
Jangan biarkan dua file aturan hidup berdampingan — agent akan nurut yang lebih gampang.
