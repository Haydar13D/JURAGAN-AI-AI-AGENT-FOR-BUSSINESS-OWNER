import asyncio
import os
import sys
from datetime import datetime, timedelta

# Pastikan path python mencakup root f:/AI-AGENT
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from app.core.database import init_db, AsyncSessionLocal
from app.services.pos_importer import POSImporterService
from app.services.analytics_engine import SalesAnalyticsEngine
from app.services.deepseek_agent import DeepSeekAgentService
from app.services.daily_reporter import DailyReporterService

async def main():
    print("=" * 70)
    print("🚀 MENJALANKAN EVALUASI PROOF OF CONCEPT (PoC) JURAGANAI")
    print("=" * 70)

    # 1. Inisialisasi Database
    print("\n[Langkah 1/5] Inisialisasi Skema Basis Data...")
    await init_db()
    print("✅ Skema database berhasil dibuat.")

    # 2. Impor Dataset Sintetis POS
    csv_file_path = os.path.join(os.path.dirname(__file__), "..", "data", "synthetic", "pos_transactions_october_2026.csv")
    if not os.path.exists(csv_file_path):
        print(f"❌ File {csv_file_path} belum ditemukan!")
        return

    print(f"\n[Langkah 2/5] Mengimpor Dataset POS dari {os.path.basename(csv_file_path)}...")
    with open(csv_file_path, "rb") as f:
        file_bytes = f.read()

    async with AsyncSessionLocal() as session:
        summary = await POSImporterService.import_file(
            session=session,
            file_bytes=file_bytes,
            filename=os.path.basename(csv_file_path),
            outlet_id=1
        )
        print(f"✅ Import Selesai:")
        print(f"   • Total Baris Dibaca: {summary.total_rows_read}")
        print(f"   • Total Transaksi Berhasil: {summary.imported_transactions} nota")
        print(f"   • Total Item Menu: {summary.imported_items} item")
        print(f"   • Total Omzet Terimpor: Rp{summary.total_revenue_imported:,.0f}")
        print(f"   • Rentang Waktu: {summary.earliest_transaction} s/d {summary.latest_transaction}")

    # 3. Uji Coba Sales Analytics Engine
    print("\n[Langkah 3/5] Menguji Sales Analytics Engine...")
    today = datetime.now().date()
    fourteen_days_ago = today - timedelta(days=14)
    start_str = fourteen_days_ago.strftime("%Y-%m-%d")
    end_str = today.strftime("%Y-%m-%d")

    async with AsyncSessionLocal() as session:
        overview = await SalesAnalyticsEngine.get_sales_overview(session, 1, start_str, end_str)
        print(f"   • GMV 14 Hari: Rp{overview.total_gmv:,.0f}")
        print(f"   • Total Transaksi: {overview.total_transactions}")
        print(f"   • Average Order Value (AOV): Rp{overview.average_order_value:,.0f}")
        print(f"   • Total Item Terjual: {overview.total_items_sold} pcs")

        top_products = await SalesAnalyticsEngine.get_top_products(session, 1, start_str, end_str, limit=3, sort_by="qty")
        print("\n   • Top 3 Menu Terlaris:")
        for p in top_products:
            print(f"     #{p.rank} {p.product_name}: {p.quantity_sold} terjual (Rp{p.total_revenue:,.0f})")

        slow_products = await SalesAnalyticsEngine.get_slow_moving_products(session, 1, start_str, end_str, limit=2)
        print("\n   • Menu Paling Sepi (Deadstock Warning):")
        for p in slow_products:
            print(f"     - {p.product_name}: {p.quantity_sold} terjual (Rp{p.total_revenue:,.0f})")

        peaks = await SalesAnalyticsEngine.get_peak_hours(session, 1, end_str)
        top_peaks = [p for p in peaks if p.transaction_count > 0][:2]
        if top_peaks:
            print("\n   • Peak Hours Hari Ini:")
            for p in top_peaks:
                print(f"     - Jam {p.hour_label}: {p.transaction_count} transaksi")

        pay_methods = await SalesAnalyticsEngine.get_payment_method_stats(session, 1, start_str, end_str)
        print("\n   • Pembagian Metode Pembayaran:")
        for m in pay_methods[:3]:
            print(f"     - {m.payment_method}: {m.transaction_count} tx ({m.percentage}%)")

    # 4. Uji Coba AI Agent Tool Calling
    print("\n[Langkah 4/5] Menguji AI Agent Percakapan (Tool Calling Dispatcher)...")
    test_queries = [
        "Berapa total omzet dan rata-rata belanja per nota minggu ini?",
        "Menu apa yang paling laku dan jam berapa paling ramai?",
        "Bandingkan penjualan minggu ini dengan minggu lalu"
    ]

    async with AsyncSessionLocal() as session:
        for q in test_queries:
            print(f"\n💬 USER: \"{q}\"")
            resp = await DeepSeekAgentService.chat(session, q, outlet_id=1)
            print(f"🔧 TOOLS EXECUTED: {resp.tools_executed}")
            print(f"🤖 JURAGAN AI:\n{resp.reply}")

    # 5. Uji Coba Automated Daily Reporting
    print("\n[Langkah 5/5] Menguji Format Laporan Penjualan Harian Otomatis...")
    async with AsyncSessionLocal() as session:
        report = await DailyReporterService.generate_daily_report_text(session, 1, end_str)
        print(f"\n📱 PREVIEW NOTIFIKASI TELEGRAM:\n{report}")

    print("\n" + "=" * 70)
    print("🎉 SELURUH PENGUJIAN PoC JURAGANAI SELESAI & BERJALAN DENGAN SUKSES!")
    print("=" * 70)

if __name__ == "__main__":
    asyncio.run(main())
