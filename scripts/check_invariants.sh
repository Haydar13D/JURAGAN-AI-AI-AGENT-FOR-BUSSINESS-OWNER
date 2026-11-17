#!/usr/bin/env bash
# scripts/check_invariants.sh — gate untuk AGENTS.md
#
# Pemakaian:
#   bash scripts/check_invariants.sh
#
# Exit code:
#   0 = semua invarian lolos
#   1 = ada pelanggaran (detail dicetak)
#   2 = dijalankan dari direktori yang salah
#
# PENTING: di commit awal, script ini WAJAR MERAH. Itu baseline, bukan kegagalan.
# Catat jumlahnya di MIGRATION-BACKLOG.md, lalu pastikan angkanya TURUN setiap task.
# Jangan "memperbaiki" script ini agar hijau — perbaiki kodenya.
#
# Cakupan: hanya app/. Mock dan data sintetis di tests/ memang diizinkan (lihat I3).

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 2

if [ ! -d app ]; then
  echo "✗ Dijalankan dari direktori yang salah: app/ tidak ditemukan di $ROOT"
  exit 2
fi

FAIL=0
TOTAL=0

check() {
  local id="$1" label="$2" pattern="$3"
  local out count

  out="$(grep -rnE "$pattern" app --include='*.py' 2>/dev/null || true)"

  if [ -n "$out" ]; then
    count="$(printf '%s\n' "$out" | wc -l | tr -d ' ')"
    TOTAL=$((TOTAL + count))
    FAIL=$((FAIL + 1))
    printf '✗ %-4s %s (%s baris)\n' "$id" "$label" "$count"
    printf '%s\n' "$out" | sed 's/^/         /'
    echo
  else
    printf '✓ %-4s %s\n' "$id" "$label"
  fi
}

echo "check_invariants — $(basename "$ROOT")"
echo "────────────────────────────────────────────────────────"

# I1 — satu registry tool
check "I1" "tidak ada daftar tool kedua (AGENT_TOOLS / if-elif)" \
  'AGENT_TOOLS|elif tool_name =='

# I2 — schema di-generate, bukan ditulis tangan
LITERAL_COUNT="$(grep -rn '"type": *"function"' app --include='*.py' 2>/dev/null | wc -l | tr -d ' ')"
if [ "$LITERAL_COUNT" -gt 1 ]; then
  TOTAL=$((TOTAL + LITERAL_COUNT))
  FAIL=$((FAIL + 1))
  printf '✗ %-4s %s (%s baris)\n' "I2" "schema function-calling ditulis tangan di banyak tempat" "$LITERAL_COUNT"
  grep -rn '"type": *"function"' app --include='*.py' 2>/dev/null | sed 's/^/         /'
  echo
else
  printf '✓ %-4s %s\n' "I2" "schema function-calling hanya di satu adapter"
fi

# I3 — tidak ada data sintetis di jalur produksi
check "I3" "tidak ada angka/timestamp karangan" \
  'base_q = curr_q \* 2|change_pct = -50|unit_price ?= ?20000|margin_pct ?= ?62\.5|2026-10-13'

# I4 — state lewat repository
check "I4" "tidak ada in-memory store di level modul" \
  '_(WORKFLOWS|RUNS|PROPOSALS|PENDING_ACTIONS)_STORE|cls\._PROPOSALS'

# I7 — tidak ada fallback yang berpura-pura
check "I7" "tidak ada fallback palsu (local agent / rule-based patch)" \
  '_fallback_local_agent|_rule_based_patch_parser'

# I10 — versi tool bukan string sort
check "I10" "versi tool tidak di-sort sebagai string" \
  'sorted\(versions\.keys\(\)'

# I12 — tidak menelan error
# Catatan: hanya menangkap bentuk satu baris (`except ...: pass`).
# Bentuk multi-baris tetap harus dicek manual saat review.
check "I12" "tidak ada except ...: pass" \
  'except[^:]*:[[:space:]]*pass'

echo "────────────────────────────────────────────────────────"

if [ "$FAIL" -eq 0 ]; then
  echo "✓ Semua invarian lolos."
  exit 0
fi

echo "✗ $FAIL grup invarian dilanggar — total $TOTAL baris."
echo "  Aturannya ada di AGENTS.md. Perbaiki kodenya, bukan script ini."
exit 1
