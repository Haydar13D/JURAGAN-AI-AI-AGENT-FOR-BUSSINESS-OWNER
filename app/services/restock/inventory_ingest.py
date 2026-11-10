"""Inventory Ingest & Aggregator Tool for UMKM Restock Pipeline.

Reads cleaned inventory dataframes or transactional stock mutation ledgers
and extracts the latest active stock per unique SKU/Item.
"""

from typing import Any, Dict, List, Optional
import pandas as pd
from app.services.profiler.data_profiler_service import DataProfilerService


def extract_current_inventory_status(
    df: pd.DataFrame,
    sku_col: Optional[str] = None,
    name_col: Optional[str] = None,
    stock_col: Optional[str] = None,
    time_col: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Normalizes and extracts current inventory state.

    If the dataframe is transactional (multiple rows per SKU, like fixture_02),
    it groups by SKU/Name and takes the most recent row based on time_col,
    or the last entry if no timestamp is present.
    """
    cleaned_df, detected_units = DataProfilerService.clean_inventory_dataframe(df)

    # Autodetect column names if not provided
    cols = cleaned_df.columns.tolist()

    if not sku_col:
        for c in cols:
            if any(term in c.lower() for term in ["kode", "sku", "code", "id_barang", "id_bahan"]):
                sku_col = c
                break

    if not name_col:
        for c in cols:
            if any(term in c.lower() for term in ["nama", "item", "produk", "bahan", "barang"]):
                name_col = c
                break

    if not stock_col:
        # Prioritize 'stok akhir', then 'sisa', then 'stok', then 'current_stock'
        for c in cols:
            cl = c.lower()
            if "stok akhir" in cl or "sisa stok" in cl or "saldo akhir" in cl:
                stock_col = c
                break
        if not stock_col:
            for c in cols:
                cl = c.lower()
                if "stok" in cl or "sisa" in cl or "qty" in cl:
                    stock_col = c
                    break

    if not time_col:
        for c in cols:
            cl = c.lower()
            if any(term in cl for term in ["update", "tanggal", "waktu", "date", "timestamp"]):
                time_col = c
                break

    if not stock_col:
        raise ValueError(f"Could not identify a stock/quantity column in headers: {cols}")

    group_col = sku_col if sku_col else name_col
    if not group_col:
        # Fallback: treat each row as a distinct item
        items = []
        unit = detected_units.get(stock_col, "pcs")
        for idx, row in cleaned_df.iterrows():
            items.append({
                "item_id": f"ITEM-{idx+1}",
                "item_name": str(row.get(name_col, f"Item {idx+1}")),
                "current_stock": float(row[stock_col]) if pd.notna(row[stock_col]) else 0.0,
                "unit": unit,
            })
        return items

    # If transactional, sort by time_col if available, then drop duplicates keeping last
    working_df = cleaned_df.copy()
    if time_col and time_col in working_df.columns:
        # Try converting to datetime for proper sorting
        try:
            working_df["_dt_sort"] = pd.to_datetime(working_df[time_col], errors="coerce")
            working_df = working_df.sort_values(by="_dt_sort", ascending=True)
        except Exception:
            pass

    # Take the last row for each unique item
    latest_rows = working_df.drop_duplicates(subset=[group_col], keep="last")

    unit = detected_units.get(stock_col, "pcs")
    results = []
    for _, row in latest_rows.iterrows():
        sku_val = str(row[sku_col]) if sku_col and pd.notna(row[sku_col]) else None
        name_val = str(row[name_col]) if name_col and pd.notna(row[name_col]) else (sku_val or "Unknown")
        stock_val = float(row[stock_col]) if pd.notna(row[stock_col]) else 0.0

        results.append({
            "item_id": sku_val or name_val,
            "item_name": name_val,
            "current_stock": stock_val,
            "unit": unit,
        })

    return results

