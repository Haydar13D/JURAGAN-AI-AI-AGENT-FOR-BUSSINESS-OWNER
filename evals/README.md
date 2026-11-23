# JuraganAI Eval Harness (T16)

Eval harness untuk mengukur perilaku model dan pipeline `run_agent()` secara objektif menggunakan `evals/golden_set.jsonl` (42 kasus di 6 kategori: `analytics`, `inventory`, `pricing`, `workflow`, `ambiguity`, `safety`).

## Menjalankan Eval

### 1. Mode Offline (CI / Tanpa Network)
Menggunakan fake LLM deterministik dan fixture tool lokal untuk memvalidasi keseluruhan alur `run_agent()`, wire name adapter, dan gate `APPROVAL_REQUIRED`:

```bash
python evals/run_eval.py --offline
```

### 2. Mode Live (Evaluasi Model Nyata)
Mengirim pertanyaan dari `golden_set.jsonl` ke provider LLM aktif di `.env` (`AtmoRouter` / `DeepSeek` / `OpenRouter`) dan menyimpan transkrip serta skor:

```bash
python evals/run_eval.py --live --min-accuracy 0.8
```

## Output & Metrik

Laporan tersimpan di `evals/reports/<YYYY-MM-DD>-<sha>.json` dengan field utama:
- `tool_selection_accuracy`: rasio kasus yang memanggil semua `expected_tools` yang diharapkan (dan tanpa tool terlarang saat `expected_tools == []`).
- `forbidden_violation_rate`: rasio kasus yang memanggil tool di `forbidden_tools`.
- `fact_match_rate`: rasio kecocokan `expected_facts` (`expected_status` atau `reply_keywords`).
- `status_distribution`: distribusi status akhir (`DONE`, `APPROVAL_REQUIRED`, dll.).
- `avg_latency_ms` & `avg_cost_idr`: rata-rata latensi dan estimasi biaya (`null` bila offline atau provider tidak mengirim `usage`).

Script keluar dengan **exit code `1`** jika `forbidden_violation_rate > 0` atau `tool_selection_accuracy < --min-accuracy`.
