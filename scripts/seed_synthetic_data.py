"""
scripts/seed_synthetic_data.py
Script untuk mengimpor dataset sintetis 60 hari ke SQLite database `juragan_ai.db`.
Mengisi:
1. Outlet: 'Kopi Kenangan Senja (Cabang Utama)'
2. Produk & Kategori dengan HPP (cost_price) dan Stock nyata sesuai PRD
3. 8.100+ transaksi lengkap dari CSV data/synthetic_pos_60days.csv
"""

import os
import sys
import asyncio
from datetime import datetime
import pandas as pd
from sqlalchemy import select, delete

# Setup path agar bisa import app modules
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from app.core.database import AsyncSessionLocal, init_db
from app.models.outlet import Outlet
from app.models.product import Category, Product
from app.models.transaction import Transaction, TransactionItem
from scripts.generate_synthetic_pos import CATALOG, OUTPUT_CSV, generate_dataset, export_to_csv

async def seed_data():
    print("[*] Menginisialisasi skema database...")
    await init_db()

    # Pastikan file CSV tersedia
    if not os.path.exists(OUTPUT_CSV):
        print("[*] File CSV tidak ditemukan, men-generate dataset 60 hari...")
        txs = generate_dataset(days_count=60)
        export_to_csv(txs, OUTPUT_CSV)

    print(f"[*] Membaca data CSV dari {OUTPUT_CSV}...")
    df = pd.read_csv(OUTPUT_CSV)

    async with AsyncSessionLocal() as session:
        # 1. Pastikan Outlet Demo Ada
        outlet_res = await session.execute(select(Outlet).where(Outlet.name == "Kopi Kenangan Senja"))
        outlet = outlet_res.scalar_one_or_none()
        if not outlet:
            outlet = Outlet(
                name="Kopi Kenangan Senja",
                address="Jl. Senopati No. 45, Jakarta Selatan",
                telegram_chat_id=None
            )
            session.add(outlet)
            await session.flush()
            print(f"[OK] Outlet dibuat: ID={outlet.id}, Name={outlet.name}")
        else:
            print(f"[OK] Menggunakan outlet yang ada: ID={outlet.id}")

        outlet_id = outlet.id

        # 2. Setup Kategori dan Produk dengan Cost Price & Stock
        category_map = {}
        product_map = {}

        for item in CATALOG:
            cat_name = item["category"]
            if cat_name not in category_map:
                cat_res = await session.execute(
                    select(Category).where(Category.outlet_id == outlet_id, Category.name == cat_name)
                )
                cat = cat_res.scalar_one_or_none()
                if not cat:
                    cat = Category(outlet_id=outlet_id, name=cat_name)
                    session.add(cat)
                    await session.flush()
                category_map[cat_name] = cat.id

            prod_name = item["name"]
            prod_res = await session.execute(
                select(Product).where(Product.outlet_id == outlet_id, Product.name == prod_name)
            )
            prod = prod_res.scalar_one_or_none()
            if not prod:
                prod = Product(
                    outlet_id=outlet_id,
                    category_id=category_map[cat_name],
                    name=prod_name,
                    sku=item["id"],
                    base_price=float(item["price"]),
                    cost_price=float(item["cost"]),
                    stock=int(item["stock"])
                )
                session.add(prod)
                await session.flush()
            else:
                # Update cost_price & stock
                prod.cost_price = float(item["cost"])
                prod.stock = int(item["stock"])
                session.add(prod)
            
            product_map[prod_name] = prod

        await session.commit()
        print(f"[OK] {len(product_map)} master produk & kategori tersimpan dengan margin & stock.")

        # 3. Cek apakah transaksi sudah diimpor
        force_reseed = "--force" in sys.argv
        if force_reseed:
            print("[*] Parameter --force terdeteksi, membersihkan transaksi lama...")
            await session.execute(delete(Transaction).where(Transaction.outlet_id == outlet_id))
            await session.commit()
            print("[OK] Transaksi lama berhasil dibersihkan.")
        else:
            tx_count_res = await session.execute(
                select(Transaction).where(Transaction.outlet_id == outlet_id)
            )
            existing_txs = len(tx_count_res.scalars().all())
            if existing_txs > 500:
                print(f"[OK] Outlet sudah memiliki {existing_txs} transaksi. Melewati import ulang.")
                return

        print(f"[*] Mengimpor {len(df)} baris transaksi dari CSV...")
        
        # Kelompokkan baris berdasarkan transaction_id
        grouped = df.groupby("transaction_id")
        created_tx = 0

        for inv_id, group in grouped:
            first_row = group.iloc[0]
            t_time = datetime.strptime(str(first_row["occurred_at"]).strip(), "%Y-%m-%d %H:%M:%S")
            p_method = str(first_row.get("payment_method", "QRIS"))

            total_amount = float(group["total_amount"].sum())
            tx = Transaction(
                outlet_id=outlet_id,
                invoice_number=str(inv_id),
                transaction_time=t_time,
                subtotal=total_amount,
                discount=0.0,
                tax=0.0,
                service_charge=0.0,
                total_amount=total_amount,
                payment_method=p_method,
                status="COMPLETED"
            )
            session.add(tx)
            await session.flush()

            for _, row in group.iterrows():
                p_name = str(row["product_name"])
                matched_prod = product_map.get(p_name)
                prod_id = matched_prod.id if matched_prod else None
                qty = int(row["quantity"])
                u_price = float(row["unit_price"])
                sub = float(row["total_amount"])

                item = TransactionItem(
                    transaction_id=tx.id,
                    product_id=prod_id,
                    product_name=p_name,
                    quantity=qty,
                    unit_price=u_price,
                    discount=0.0,
                    subtotal=sub
                )
                session.add(item)

            created_tx += 1
            if created_tx % 500 == 0:
                await session.commit()
                print(f"    -> {created_tx} transaksi tersimpan...")

        await session.commit()
        print(f"[OK] Selesai! Total {created_tx} transaksi berhasil di-seed ke database.")

if __name__ == "__main__":
    asyncio.run(seed_data())
