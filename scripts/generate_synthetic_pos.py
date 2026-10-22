"""
scripts/generate_synthetic_pos.py
Generator Dataset POS F&B Sintetis 60 Hari (PRD JuraganAI FR-01 & Sec. 11)
- Seed tetap (reproducible)
- Skenario 1: 'Caramel Macchiato' drop tajam di 14 hari terakhir (-70%)
- Skenario 2: 'Croissant Almond' margin tinggi (65%) tapi slow moving (stok melimpah)
- Skenario 3: Top seller stabil (Kopi Susu Gula Aren, Americano, Rice Bowl)
- Variasi jam operasional (lunch & dinner peak) dan weekend spike (+35%)
- Menghasilkan file CSV dan opsi langsung seed ke SQLite
"""

import os
import csv
import random
from datetime import datetime, timedelta, date, time
from typing import List, Dict, Any

# Seed tetap agar reproducible (PRD FR-01)
RANDOM_SEED = 42
random.seed(RANDOM_SEED)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_CSV = os.path.join(BASE_DIR, "data", "synthetic_pos_60days.csv")

# Menu Katalog Coffee Shop
CATALOG = [
    {
        "id": "PROD-001",
        "name": "Kopi Susu Gula Aren",
        "category": "Kopi",
        "price": 20000,
        "cost": 7500,
        "stock": 500,
        "base_daily_qty": (30, 45),
        "pattern": "stable"
    },
    {
        "id": "PROD-002",
        "name": "Caramel Macchiato",
        "category": "Kopi",
        "price": 32000,
        "cost": 12000,
        "stock": 150,
        "base_daily_qty": (18, 26),
        "pattern": "drop_late"  # Drop tajam di 14 hari terakhir
    },
    {
        "id": "PROD-003",
        "name": "Americano",
        "category": "Kopi",
        "price": 18000,
        "cost": 5000,
        "stock": 300,
        "base_daily_qty": (15, 25),
        "pattern": "stable"
    },
    {
        "id": "PROD-004",
        "name": "Matcha Latte",
        "category": "Non-Kopi",
        "price": 26000,
        "cost": 9500,
        "stock": 100,
        "base_daily_qty": (10, 18),
        "pattern": "stable"
    },
    {
        "id": "PROD-005",
        "name": "Rice Bowl Ayam Sambal Matah",
        "category": "Makanan",
        "price": 38000,
        "cost": 16000,
        "stock": 80,
        "base_daily_qty": (14, 24),
        "pattern": "stable"
    },
    {
        "id": "PROD-006",
        "name": "Croissant Almond",
        "category": "Cemilan",
        "price": 35000,
        "cost": 12000,  # Margin 65.7%
        "stock": 50,
        "base_daily_qty": (0, 1),  # Slow moving
        "pattern": "slow_moving"
    },
    {
        "id": "PROD-007",
        "name": "Cireng Rujak Crispy",
        "category": "Cemilan",
        "price": 18000,
        "cost": 6000,
        "stock": 120,
        "base_daily_qty": (10, 16),
        "pattern": "stable"
    }
]

PAYMENT_METHODS = ["QRIS", "QRIS", "QRIS", "CASH", "CASH", "DEBIT_CARD", "GO-PAY", "OVO"]

def generate_dataset(days_count: int = 60, end_date: date = None) -> List[Dict[str, Any]]:
    if end_date is None:
        end_date = date.today()
    
    start_date = end_date - timedelta(days=days_count - 1)
    transactions = []
    tx_counter = 1000

    print(f"[*] Menghasilkan dataset sintetis {days_count} hari ({start_date} s/d {end_date})...")

    current_day = start_date
    day_idx = 0

    while current_day <= end_date:
        is_weekend = current_day.weekday() in [5, 6]  # Sabtu & Minggu
        weekend_multiplier = 1.35 if is_weekend else 1.0

        # Hari ke-54 s/d 60 (7 hari terakhir) adalah periode penurunan tajam Caramel Macchiato
        is_late_period = (days_count - day_idx) <= 7

        # Kumpulkan keranjang belanja harian
        day_items = []
        for prod in CATALOG:
            p_pattern = prod["pattern"]
            min_q, max_q = prod["base_daily_qty"]

            if p_pattern == "drop_late" and is_late_period:
                # Caramel Macchiato drop tajam (-75%)
                daily_qty = max(1, int(random.randint(2, 4) * (1.2 if is_weekend else 1.0)))
            elif p_pattern == "slow_moving":
                daily_qty = random.randint(min_q, max_q)
            else:
                base = random.randint(min_q, max_q)
                daily_qty = int(base * weekend_multiplier)

            for _ in range(daily_qty):
                day_items.append(prod)

        # Acak urutan item sepanjang hari
        random.shuffle(day_items)

        # Kelompokkan item ke dalam transaksi (1 s/d 3 item per transaksi)
        i = 0
        while i < len(day_items):
            basket_size = random.choices([1, 2, 3, 4], weights=[0.45, 0.35, 0.15, 0.05])[0]
            tx_items = day_items[i : i + basket_size]
            i += basket_size

            # Tentukan jam transaksi (pola F&B: 09:00 - 22:30)
            # Bobot jam: Siang (12:00-14:00) dan Malam (18:30-21:30) lebih padat
            hour_pool = (
                [9, 10, 11] * 2 +
                [12, 13] * 6 +         # Lunch peak
                [14, 15, 16] * 3 +
                [17, 18] * 4 +
                [19, 20, 21] * 7 +      # Dinner & Hangout peak
                [22] * 2
            )
            tx_hour = random.choice(hour_pool)
            tx_minute = random.randint(0, 59)
            tx_second = random.randint(0, 59)
            tx_time = datetime.combine(current_day, time(tx_hour, tx_minute, tx_second))

            tx_counter += 1
            invoice_num = f"INV-{current_day.strftime('%Y%m%d')}-{tx_counter:04d}"
            pay_method = random.choice(PAYMENT_METHODS)

            for item in tx_items:
                qty = 1
                price = item["price"]
                total = qty * price
                transactions.append({
                    "transaction_id": invoice_num,
                    "occurred_at": tx_time.strftime("%Y-%m-%d %H:%M:%S"),
                    "product_id": item["id"],
                    "product_name": item["name"],
                    "category": item["category"],
                    "quantity": qty,
                    "unit_price": price,
                    "total_amount": total,
                    "payment_method": pay_method
                })

        current_day += timedelta(days=1)
        day_idx += 1

    print(f"[OK] Berhasil menghasilkan {len(transactions)} baris transaksi dari {days_count} hari.")
    return transactions

def export_to_csv(transactions: List[Dict[str, Any]], filepath: str):
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    fieldnames = [
        "transaction_id",
        "occurred_at",
        "product_id",
        "product_name",
        "category",
        "quantity",
        "unit_price",
        "total_amount",
        "payment_method"
    ]
    with open(filepath, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(transactions)
    print(f"[OK] File CSV tersimpan di: {filepath}")

if __name__ == "__main__":
    txs = generate_dataset(days_count=60)
    export_to_csv(txs, OUTPUT_CSV)
