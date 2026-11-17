"""
tests/test_ingest_fabrication.py

T1 — Dokumentasi kebohongan data (xfail).

Tiga test di bawah mendeskripsikan perilaku yang SEHARUSNYA terjadi.
Sekarang ketiganya GAGAL, dan itu memang tujuannya. Sengaja ditandai
`xfail(strict=True)` supaya:

  - suite tetap hijau selama T2 belum dikerjakan
  - TAPI kalau test ini tiba-tiba lolos tanpa T2, pytest jadi MERAH
    (strict), jadi nggak bisa "kebetulan hijau" gara-gara bug lain

Yang diuji: `IngestPosDataTool` mengarang baseline, harga, dan kuantitas
kalau data user nggak cukup. Lihat AGENTS.md I3.

Cara pakai:
    pytest -q tests/test_ingest_fabrication.py
    → harapan: 3 xfailed

Di T2: hapus tiga marker @pytest.mark.xfail, lalu bikin hijau.
JANGAN mengubah assertion-nya supaya cocok dengan perilaku sekarang.
"""

import pytest

from app.core.tool_registry import ExecutionContext
from app.services.fnb_marketing.tools import (
    DetectOpportunitiesConfig,
    DetectOpportunitiesInput,
    DetectOpportunitiesTool,
    IngestPosDataConfig,
    IngestPosDataInput,
    IngestPosDataTool,
)


CONTEXT = ExecutionContext(
    tenant_id="tenant_test",
    workflow_id="wf_test",
    workflow_version=1,
    run_id="run_test",
    is_dry_run=True,
    actor_id="pytest",
)

# ---------------------------------------------------------------------
# Fixture: data yang memang TIDAK cukup untuk analisis tren
# ---------------------------------------------------------------------

# 1. Hanya satu periode (semua transaksi 7 hari terakhir, nggak ada pembanding).
#    Kode sekarang: baseline_qty semua 0 -> dianggap "single period" ->
#    baseline dikarang (base_q = curr_q * 2) dan penurunan dipaksa -50%.
SINGLE_PERIOD_CSV = """transaction_id,occurred_at,product_name,category,quantity,unit_price,total_amount
TX-1,2026-11-10 09:00:00,Kopi Susu,Minuman,2,20000,40000
TX-2,2026-11-11 10:00:00,Kopi Susu,Minuman,3,20000,60000
TX-3,2026-11-12 11:00:00,Matcha Latte,Minuman,1,28000,28000
TX-4,2026-11-13 12:00:00,Roti Bakar,Makanan,2,15000,30000
"""

# 2. Nggak ada kolom harga sama sekali.
#    Kode sekarang: unit_price di-default 20000.0, total_amount dihitung
#    dari harga karangan itu.
NO_PRICE_CSV = """transaction_id,occurred_at,product_name,category,quantity
TX-1,2026-11-10 09:00:00,Kopi Susu,Minuman,2
TX-2,2026-11-11 10:00:00,Roti Bakar,Makanan,1
"""

# 3. Format "Daily Report" (ada kolom Menu, tanpa tanggal).
#    Kode sekarang: baseline_qty=20, current_qty=10, qty_change_pct=-50.0,
#    dan angka omzet dipakai sebagai unit_price.
MENU_REPORT_CSV = """Daily Report
Menu,Kategori,Jumlah
Kopi Susu,Minuman,450000
Matcha Latte,Minuman,280000
"""


# ---------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------

async def test_single_period_data_is_rejected_not_fabricated():
    """Tanpa periode pembanding, sistem harus menolak — bukan mengarang baseline."""
    tool = IngestPosDataTool()
    result = await tool.execute(
        IngestPosDataConfig(),
        IngestPosDataInput(csv_content=SINGLE_PERIOD_CSV),
        CONTEXT,
    )

    assert result.status == "INSUFFICIENT_DATA", (
        "Data cuma satu periode, jadi tidak ada baseline. Sistem harus bilang "
        "INSUFFICIENT_DATA, bukan mengarang baseline 2x lipat dan penurunan -50%."
    )


async def test_missing_price_columns_are_rejected_not_defaulted():
    """Tanpa kolom harga, jangan karang harga — tolak dan sebut kolom yang hilang."""
    tool = IngestPosDataTool()
    result = await tool.execute(
        IngestPosDataConfig(),
        IngestPosDataInput(csv_content=NO_PRICE_CSV),
        CONTEXT,
    )

    assert result.status == "INSUFFICIENT_DATA", (
        "CSV tidak punya kolom harga/total. Sistem harus menolak dengan alasan "
        "kolom mana yang hilang, bukan memakai harga default 20000."
    )
    assert "harga" in (result.reason or "").lower(), (
        f"Alasan penolakan harus menyebut kolom harga, dapat: {result.reason!r}"
    )


async def test_menu_report_without_dates_is_rejected_not_fabricated():
    """Format tanpa tanggal nggak bisa dipakai buat tren — tolak, jangan karang 20 vs 10."""
    tool = IngestPosDataTool()
    result = await tool.execute(
        IngestPosDataConfig(),
        IngestPosDataInput(csv_content=MENU_REPORT_CSV),
        CONTEXT,
    )

    assert result.status == "INSUFFICIENT_DATA", (
        "Daily Report tanpa tanggal tidak punya periode pembanding. Sistem harus "
        "menolak, bukan menetapkan baseline_qty=20 / current_qty=10 / -50%."
    )


async def test_detect_opportunities_rejects_insufficient_data():
    """DetectOpportunitiesTool harus menolak input pos_data dengan status != 'OK'."""
    ingest_tool = IngestPosDataTool()
    pos_summary = await ingest_tool.execute(
        IngestPosDataConfig(),
        IngestPosDataInput(csv_content=SINGLE_PERIOD_CSV),
        CONTEXT,
    )
    assert pos_summary.status == "INSUFFICIENT_DATA"

    detect_tool = DetectOpportunitiesTool()
    batch = await detect_tool.execute(
        DetectOpportunitiesConfig(),
        DetectOpportunitiesInput(pos_data=pos_summary),
        CONTEXT,
    )
    assert batch.status == "INSUFFICIENT_DATA"
    assert batch.reason and "periode" in batch.reason.lower()
    assert batch.detected_count == 0
