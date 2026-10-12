import os
import random
import sys
from datetime import datetime, timedelta
import pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# Menu F&B Cafe & Resto Indonesia yang Realistis
MENU_ITEMS = [
    # Minuman Kopi
    {"name": "Kopi Susu Gula Aren", "category": "Kopi", "price": 18000, "popularity": 30},
    {"name": "Americano Dingin", "category": "Kopi", "price": 15000, "popularity": 18},
    {"name": "Caffe Latte", "category": "Kopi", "price": 22000, "popularity": 14},
    {"name": "Caramel Macchiato", "category": "Kopi", "price": 26000, "popularity": 10},
    {"name": "Matcha Espresso Fusion", "category": "Kopi", "price": 28000, "popularity": 8},
    # Non-Kopi
    {"name": "Matcha Latte Creamy", "category": "Non-Kopi", "price": 24000, "popularity": 16},
    {"name": "Chocolate Signature", "category": "Non-Kopi", "price": 22000, "popularity": 15},
    {"name": "Lemon Tea Segar", "category": "Non-Kopi", "price": 12000, "popularity": 12},
    {"name": "Earl Grey Milk Tea", "category": "Non-Kopi", "price": 20000, "popularity": 9},
    # Makanan Utama
    {"name": "Nasi Goreng Spesial Juragan", "category": "Makanan", "price": 32000, "popularity": 22},
    {"name": "Mie Pedas Level 2", "category": "Makanan", "price": 16000, "popularity": 25},
    {"name": "Rice Bowl Ayam Sambal Matah", "category": "Makanan", "price": 28000, "popularity": 20},
    # Cemilan & Pastry
    {"name": "Dimsum Ayam Udang (4 pcs)", "category": "Cemilan", "price": 20000, "popularity": 24},
    {"name": "Kentang Goreng Crispy", "category": "Cemilan", "price": 15000, "popularity": 19},
    {"name": "Toast Coklat Keju", "category": "Cemilan", "price": 18000, "popularity": 14},
    {"name": "Croissant Butter", "category": "Pastry", "price": 22000, "popularity": 12},
    {"name": "Pain au Chocolat", "category": "Pastry", "price": 25000, "popularity": 8},
    # Menu Kurang Laku (Slow-moving / Deadstock test)
    {"name": "Kombucha Ginger Herbal", "category": "Sehat", "price": 35000, "popularity": 1},
    {"name": "Salad Sayur Quinoa", "category": "Sehat", "price": 38000, "popularity": 2},
]

PAYMENT_METHODS = ["QRIS", "QRIS", "QRIS", "QRIS", "CASH", "CASH", "DEBIT", "GO-PAY", "SHOPEEPAY"]
CASHIERS = ["Siti (Kasir 1)", "Bambang (Kasir 2)", "Dina (Kasir 1)"]

def get_peak_hour():
    """Simulasi jam kunjungan kafe/resto: peak siang (12-14) dan peak malam (18-21)."""
    weights = [
        0, 0, 0, 0, 0, 0, 0,     # 00-06
        1, 3, 5,                 # 07-09
        8, 12,                   # 10-11
        22, 24, 18,              # 12-14 (Peak Lunch)
        10, 12, 16,              # 15-17 (Sore)
        25, 28, 22, 18,          # 18-21 (Peak Dinner/Hangout)
        8, 2                     # 22-23
    ]
    hours = list(range(24))
    return random.choices(hours, weights=weights, k=1)[0]

def generate_base_transactions(num_transactions: int = 250, days_back: int = 7) -> list:
    end_date = datetime.now()
    rows = []
    item_pool = []
    for item in MENU_ITEMS:
        item_pool.extend([item] * item["popularity"])

    invoice_counter = 2001

    for _ in range(num_transactions):
        random_days = random.uniform(0, days_back)
        tx_day = end_date - timedelta(days=random_days)
        tx_hour = get_peak_hour()
        tx_minute = random.randint(0, 59)
        tx_second = random.randint(0, 59)
        tx_time = tx_day.replace(hour=tx_hour, minute=tx_minute, second=tx_second, microsecond=0)

        invoice_id = f"INV-{tx_time.strftime('%y%m%d')}-{invoice_counter:04d}"
        invoice_counter += 1

        payment_method = random.choice(PAYMENT_METHODS)
        cashier = random.choice(CASHIERS)
        status = "COMPLETED"
        if random.random() < 0.015:
            status = "VOID"

        num_items_in_order = random.choices([1, 2, 3, 4], weights=[45, 35, 15, 5], k=1)[0]
        selected_items = random.sample(item_pool, k=min(num_items_in_order, len(item_pool)))

        for it in selected_items:
            qty = random.choices([1, 2, 3], weights=[80, 16, 4], k=1)[0]
            unit_price = it["price"]
            disc = 2000 * qty if random.random() < 0.08 else 0
            subtotal = (qty * unit_price) - disc
            rows.append({
                "invoice_id": invoice_id,
                "tx_time": tx_time,
                "name": it["name"],
                "category": it["category"],
                "qty": qty,
                "price": unit_price,
                "discount": disc,
                "subtotal": subtotal,
                "payment": payment_method,
                "status": status,
                "cashier": cashier
            })

    return rows

def generate_all_presets():
    out_dir = os.path.join(os.path.dirname(__file__), "..", "data", "synthetic")
    os.makedirs(out_dir, exist_ok=True)
    base_rows = generate_base_transactions(num_transactions=300, days_back=7)

    # 1. Preset Moka POS (Receipt Number, Date, Time, Category, Item, Quantity, Gross Sales, Discount, Net Sales, Payment Method, Collected By)
    moka_rows = []
    for r in base_rows:
        moka_rows.append({
            "Receipt Number": r["invoice_id"],
            "Date": r["tx_time"].strftime("%Y-%m-%d"),
            "Time": r["tx_time"].strftime("%H:%M:%S"),
            "Category": r["category"],
            "Item": r["name"],
            "Quantity": r["qty"],
            "Gross Sales": r["qty"] * r["price"],
            "Discount": r["discount"],
            "Net Sales": r["subtotal"],
            "Payment Method": r["payment"],
            "Collected By": r["cashier"]
        })
    df_moka = pd.DataFrame(moka_rows)
    df_moka.to_csv(os.path.join(out_dir, "moka_pos_sample.csv"), index=False)
    df_moka.to_excel(os.path.join(out_dir, "moka_pos_sample.xlsx"), index=False)
    print("✅ Moka POS sample created (CSV & XLSX)")

    # 2. Preset Pawoon POS (No Transaksi, Tanggal, Jam, Kategori Produk, Nama Produk, Jumlah, Harga Jual, Potongan, Total Bayar, Tipe Pembayaran, Kasir)
    pawoon_rows = []
    for r in base_rows:
        pawoon_rows.append({
            "No Transaksi": r["invoice_id"].replace("INV-", "PW-"),
            "Tanggal": r["tx_time"].strftime("%d/%m/%Y"),
            "Jam": r["tx_time"].strftime("%H:%M"),
            "Kategori Produk": r["category"],
            "Nama Produk": r["name"],
            "Jumlah": r["qty"],
            "Harga Jual": r["price"],
            "Potongan": r["discount"],
            "Total Bayar": r["subtotal"],
            "Tipe Pembayaran": r["payment"],
            "Kasir": r["cashier"]
        })
    df_pawoon = pd.DataFrame(pawoon_rows)
    df_pawoon.to_csv(os.path.join(out_dir, "pawoon_pos_sample.csv"), index=False)
    df_pawoon.to_excel(os.path.join(out_dir, "pawoon_pos_sample.xlsx"), index=False)
    print("✅ Pawoon POS sample created (CSV & XLSX)")

    # 3. Preset Majoo POS (Nomor Pesanan, Waktu Transaksi, Kategori, Menu, Qty, Harga Normal, Diskon Produk, Total Akhir, Tipe Bayar, Nama Kasir)
    majoo_rows = []
    for r in base_rows:
        majoo_rows.append({
            "Nomor Pesanan": r["invoice_id"].replace("INV-", "MJ-"),
            "Waktu Transaksi": r["tx_time"].strftime("%Y-%m-%d %H:%M:%S"),
            "Kategori": r["category"],
            "Menu": r["name"],
            "Qty": r["qty"],
            "Harga Normal": r["price"],
            "Diskon Produk": r["discount"],
            "Total Akhir": r["subtotal"],
            "Tipe Bayar": r["payment"],
            "Nama Kasir": r["cashier"]
        })
    df_majoo = pd.DataFrame(majoo_rows)
    df_majoo.to_csv(os.path.join(out_dir, "majoo_pos_sample.csv"), index=False)
    df_majoo.to_excel(os.path.join(out_dir, "majoo_pos_sample.xlsx"), index=False)
    print("✅ Majoo POS sample created (CSV & XLSX)")

if __name__ == "__main__":
    generate_all_presets()
