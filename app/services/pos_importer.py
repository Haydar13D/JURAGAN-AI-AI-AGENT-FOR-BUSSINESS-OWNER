import io
import re
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple
import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import logger
from app.models.outlet import Outlet
from app.models.product import Category, Product
from app.models.transaction import Transaction, TransactionItem
from app.models.import_job import ImportJob
from app.schemas.pos_import import POSImportSummary

COLUMN_ALIASES = {
    "invoice_number": [
        "invoice", "invoice_number", "no_struk", "nomor_struk", "order_id",
        "receipt_number", "receipt_no", "transaction_id", "id_transaksi",
        "no_nota", "no_transaksi", "nomor_pesanan", "transaksi_id"
    ],
    "transaction_time": [
        "datetime", "transaction_time", "tanggal_transaksi", "waktu_transaksi", "created_at"
    ],
    "date_only": ["date", "tanggal", "order_date"],
    "time_only": ["time", "waktu", "jam", "order_time"],
    "product_name": [
        "product_name", "item_name", "nama_produk", "nama_menu", "menu", "item", "produk"
    ],
    "category_name": [
        "category", "kategori", "category_name", "item_category", "kategori_produk"
    ],
    "quantity": ["quantity", "qty", "jumlah", "banyak"],
    "unit_price": [
        "price", "unit_price", "harga_satuan", "harga", "harga_jual", "harga_normal"
    ],
    "discount": ["discount", "diskon", "potongan", "diskon_produk", "discount_amount"],
    "subtotal": [
        "subtotal", "total", "total_price", "total_harga", "total_bayar",
        "gross_sales", "net_sales", "total_akhir", "total_amount"
    ],
    "payment_method": [
        "payment_method", "metode_pembayaran", "tipe_pembayaran", "payment_type",
        "cara_bayar", "tipe_bayar", "pembayaran"
    ],
    "status": ["status", "order_status", "status_transaksi"],
    "cashier_name": [
        "cashier", "kasir", "cashier_name", "operator", "collected_by", "served_by", "nama_kasir"
    ]
}

def detect_pos_vendor(original_cols: List[str]) -> str:
    cols_lower = [str(c).lower().strip() for c in original_cols]
    col_str = " ".join(cols_lower)
    if any(k in col_str for k in ["receipt", "gross sales", "collected by", "net sales"]):
        return "Moka POS"
    elif any(k in col_str for k in ["no transaksi", "kategori produk"]):
        return "Pawoon POS"
    elif any(k in col_str for k in ["nomor pesanan", "tipe bayar", "diskon produk"]):
        return "Majoo POS"
    elif any(k in col_str for k in ["invoice no", "order date"]):
        return "ESB / Olsera POS"
    return "Generic Standard POS"

def normalize_column_name(col: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_]", "_", str(col).strip().lower())
    for target, aliases in COLUMN_ALIASES.items():
        if cleaned in aliases or any(alias in cleaned for alias in aliases):
            return target
    return cleaned

def parse_datetime(val: Any) -> datetime:
    if isinstance(val, datetime):
        return val
    val_str = str(val).strip()
    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y",
        "%d-%m-%Y %H:%M:%S",
        "%d-%m-%Y %H:%M",
        "%d-%m-%Y",
        "%Y/%m/%d %H:%M:%S"
    ]
    for fmt in formats:
        try:
            return datetime.strptime(val_str, fmt)
        except ValueError:
            continue
    try:
        return pd.to_datetime(val_str).to_pydatetime()
    except Exception:
        return datetime.now()

class POSImporterService:

    @staticmethod
    async def import_file(
        session: AsyncSession,
        file_bytes: bytes,
        filename: str,
        outlet_id: int = 1
    ) -> POSImportSummary:
        # 1. Pastikan outlet exists
        outlet_stmt = select(Outlet).where(Outlet.id == outlet_id)
        res = await session.execute(outlet_stmt)
        outlet = res.scalar_one_or_none()
        if not outlet:
            outlet = Outlet(id=outlet_id, name="Kopi & Resto Juragan")
            session.add(outlet)
            await session.flush()

        # 2. Catat audit job
        file_ext = filename.split(".")[-1].upper() if "." in filename else "CSV"
        job = ImportJob(
            outlet_id=outlet_id,
            filename=filename,
            file_type=file_ext,
            status="PROCESSING"
        )
        session.add(job)
        await session.flush()

        try:
            # 3. Baca dataframe
            if filename.lower().endswith((".xlsx", ".xls")):
                df = pd.read_excel(io.BytesIO(file_bytes))
            else:
                # Coba delimiter koma atau titik koma
                try:
                    df = pd.read_csv(io.BytesIO(file_bytes), sep=",")
                    if len(df.columns) <= 1:
                        df = pd.read_csv(io.BytesIO(file_bytes), sep=";")
                except Exception:
                    df = pd.read_csv(io.BytesIO(file_bytes), sep=";")

            total_rows_read = len(df)
            if total_rows_read == 0:
                raise ValueError("File kosong tanpa data.")

            # 4. Deteksi Vendor POS
            detected_vendor = detect_pos_vendor(list(df.columns))
            logger.info(f"Terdeteksi format POS: {detected_vendor} untuk file {filename}")

            # 5. Map columns safely (cegah kolom ganda jika ada Gross Sales dan Net Sales)
            assigned_targets = set()
            rename_map = {}
            for col in df.columns:
                norm = normalize_column_name(col)
                if norm in COLUMN_ALIASES:
                    if norm not in assigned_targets:
                        rename_map[col] = norm
                        assigned_targets.add(norm)
            df.rename(columns=rename_map, inplace=True)
            # Pastikan tidak ada kolom duplikat pada DataFrame
            df = df.loc[:, ~df.columns.duplicated()]

            # Penggabungan jika format POS memisahkan kolom tanggal dan jam (seperti Moka / Pawoon)
            if "transaction_time" not in df.columns:
                if "date_only" in df.columns and "time_only" in df.columns:
                    df["transaction_time"] = df["date_only"].astype(str) + " " + df["time_only"].astype(str)
                elif "date_only" in df.columns:
                    df["transaction_time"] = df["date_only"]

            # Validasi kolom wajib minimal
            if "invoice_number" not in df.columns:
                # generate default invoice grouping jika tidak ada invoice
                df["invoice_number"] = [f"INV-AUTO-{i+1:05d}" for i in range(len(df))]
            if "product_name" not in df.columns:
                df["product_name"] = "Item POS"
            if "quantity" not in df.columns:
                df["quantity"] = 1
            if "unit_price" not in df.columns:
                df["unit_price"] = 0.0

            # 5. Cleaning values
            df["quantity"] = pd.to_numeric(df["quantity"], errors="coerce").fillna(1).astype(int)
            df["unit_price"] = pd.to_numeric(df["unit_price"], errors="coerce").fillna(0.0).astype(float)
            
            if "discount" in df.columns:
                df["discount"] = pd.to_numeric(df["discount"], errors="coerce").fillna(0.0).astype(float)
            else:
                df["discount"] = 0.0

            if "subtotal" in df.columns:
                df["subtotal"] = pd.to_numeric(df["subtotal"], errors="coerce").fillna(df["quantity"] * df["unit_price"]).astype(float)
            else:
                df["subtotal"] = (df["quantity"] * df["unit_price"]) - df["discount"]

            if "payment_method" not in df.columns:
                df["payment_method"] = "QRIS"
            df["payment_method"] = df["payment_method"].fillna("QRIS").astype(str).str.upper()

            if "status" not in df.columns:
                df["status"] = "COMPLETED"
            df["status"] = df["status"].fillna("COMPLETED").astype(str).str.upper()

            # Filter out VOID/REFUND jika diperlukan, tapi kita simpan status aslinya
            status_map = {
                "SELESAI": "COMPLETED",
                "SUCCESS": "COMPLETED",
                "BATAL": "VOID",
                "CANCELLED": "VOID"
            }
            df["status"] = df["status"].replace(status_map)

            # 6. Kategori & Produk Caching
            cat_stmt = select(Category).where(Category.outlet_id == outlet_id)
            cat_res = await session.execute(cat_stmt)
            existing_categories = {c.name.lower(): c.id for c in cat_res.scalars().all()}

            prod_stmt = select(Product).where(Product.outlet_id == outlet_id)
            prod_res = await session.execute(prod_stmt)
            existing_products = {p.name.lower(): p.id for p in prod_res.scalars().all()}

            # 7. Grouping by Invoice
            grouped = df.groupby("invoice_number")
            imported_trans_count = 0
            imported_item_count = 0
            earliest_dt = None
            latest_dt = None
            total_rev = 0.0

            for invoice_num, group in grouped:
                # Cek apakah invoice sudah pernah diimpor
                check_inv = await session.execute(
                    select(Transaction).where(
                        Transaction.outlet_id == outlet_id,
                        Transaction.invoice_number == str(invoice_num)
                    )
                )
                existing_tx = check_inv.scalar_one_or_none()
                if existing_tx:
                    # Skip transaksi yang sudah ada untuk mencegah duplikasi
                    continue

                first_row = group.iloc[0]
                tx_time = parse_datetime(first_row.get("transaction_time", datetime.now()))
                if earliest_dt is None or tx_time < earliest_dt:
                    earliest_dt = tx_time
                if latest_dt is None or tx_time > latest_dt:
                    latest_dt = tx_time

                tx_subtotal = float(group["subtotal"].sum())
                tx_discount = float(group["discount"].sum()) if "discount" in group.columns else 0.0
                tx_total = tx_subtotal
                tx_status = str(first_row.get("status", "COMPLETED"))
                tx_payment = str(first_row.get("payment_method", "QRIS"))
                tx_cashier = str(first_row.get("cashier_name", "Kasir 1")) if "cashier_name" in group.columns else None

                transaction = Transaction(
                    outlet_id=outlet_id,
                    invoice_number=str(invoice_num),
                    transaction_time=tx_time,
                    subtotal=tx_subtotal,
                    discount=tx_discount,
                    tax=0.0,
                    service_charge=0.0,
                    total_amount=tx_total,
                    payment_method=tx_payment,
                    status=tx_status,
                    cashier_name=tx_cashier
                )
                session.add(transaction)
                await session.flush() # Mendapatkan transaction.id

                if tx_status == "COMPLETED":
                    total_rev += tx_total

                # Buat Transaction Items
                for _, item_row in group.iterrows():
                    p_name = str(item_row.get("product_name", "Menu")).strip()
                    c_name = str(item_row.get("category_name", "Umum")).strip() if "category_name" in item_row else "Umum"
                    p_qty = int(item_row["quantity"])
                    p_price = float(item_row["unit_price"])
                    p_disc = float(item_row.get("discount", 0.0))
                    p_subtotal = float(item_row["subtotal"])

                    # Pastikan kategori ada
                    c_key = c_name.lower()
                    if c_key not in existing_categories:
                        new_cat = Category(outlet_id=outlet_id, name=c_name)
                        session.add(new_cat)
                        await session.flush()
                        existing_categories[c_key] = new_cat.id
                    category_id = existing_categories[c_key]

                    # Pastikan produk ada
                    p_key = p_name.lower()
                    if p_key not in existing_products:
                        new_prod = Product(
                            outlet_id=outlet_id,
                            category_id=category_id,
                            name=p_name,
                            base_price=p_price
                        )
                        session.add(new_prod)
                        await session.flush()
                        existing_products[p_key] = new_prod.id
                    product_id = existing_products[p_key]

                    item = TransactionItem(
                        transaction_id=transaction.id,
                        product_id=product_id,
                        product_name=p_name,
                        quantity=p_qty,
                        unit_price=p_price,
                        discount=p_disc,
                        subtotal=p_subtotal
                    )
                    session.add(item)
                    imported_item_count += 1

                imported_trans_count += 1

            # Update job status
            job.status = "COMPLETED"
            job.total_rows = total_rows_read
            job.imported_rows = imported_trans_count
            job.completed_at = datetime.now()

            await session.commit()

            return POSImportSummary(
                job_id=job.id,
                filename=filename,
                detected_vendor=detected_vendor,
                status="COMPLETED",
                total_rows_read=total_rows_read,
                imported_transactions=imported_trans_count,
                imported_items=imported_item_count,
                skipped_rows=total_rows_read - imported_item_count,
                earliest_transaction=earliest_dt,
                latest_transaction=latest_dt,
                total_revenue_imported=round(total_rev, 2)
            )

        except Exception as e:
            await session.rollback()
            job.status = "FAILED"
            job.error_message = str(e)
            session.add(job)
            await session.commit()
            logger.error(f"Gagal mengimpor file POS {filename}: {str(e)}", exc_info=True)
            raise

    @staticmethod
    async def get_import_jobs(
        session: AsyncSession,
        outlet_id: int = 1,
        limit: int = 20
    ) -> List[ImportJob]:
        stmt = select(ImportJob).where(
            ImportJob.outlet_id == outlet_id
        ).order_by(
            ImportJob.started_at.desc()
        ).limit(limit)
        res = await session.execute(stmt)
        return list(res.scalars().all())
