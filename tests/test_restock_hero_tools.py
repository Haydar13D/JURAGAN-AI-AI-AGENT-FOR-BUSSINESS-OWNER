"""End-to-end tests for the Hero Restock Tool Pack against real fixture_02_kartu_stok_bahan.csv."""

import os
import pandas as pd
import pytest

from app.services.restock.inventory_ingest import extract_current_inventory_status
from app.services.restock.inventory_filter import filter_low_stock_items
from app.services.restock.po_generator import SupplierResolver, generate_po_whatsapp_draft


FIXTURE_CSV_PATH = os.path.join(
    os.path.dirname(__file__), "fixtures", "real_umkm", "dev", "fixture_02_kartu_stok_bahan.csv"
)


def test_extract_current_inventory_status_on_real_fixture():
    """Verify inventory ingest extracts distinct items and latest stock from transactional ledger."""
    df = pd.read_csv(FIXTURE_CSV_PATH)
    items = extract_current_inventory_status(df)

    # 1. Total distinct materials in fixture: Beef, Saus tomat, Roti bun, Keju slice, Paper bag = 5 items
    assert len(items) == 5

    item_names = [it["item_name"] for it in items]
    assert "Beef" in item_names
    assert "Roti bun (burger)" in item_names
    assert "Keju slice" in item_names

    # 2. Check that latest stock for 'Beef' matches the final update in fixture
    # In fixture_02, final update for Beef is 915.00 pcs
    beef_item = next(it for it in items if "Beef" in it["item_name"])
    assert beef_item["current_stock"] == 915.0

    # Roti bun final update: 83.00 pcs
    roti_item = next(it for it in items if "Roti bun" in it["item_name"])
    assert roti_item["current_stock"] == 83.0


def test_filter_low_stock_items():
    """Verify safety stock filtering correctly flags items below threshold."""
    df = pd.read_csv(FIXTURE_CSV_PATH)
    items = extract_current_inventory_status(df)

    # Roti bun is 83.0 pcs. If threshold is 100.0 pcs, it should be flagged!
    res = filter_low_stock_items(
        items,
        default_safety_stock=100.0,
        safety_stock_overrides={"BHN-2": 100.0, "Beef": 500.0}
    )

    assert res["total_items"] == 5
    # Roti bun (83 <= 100) and Keju slice (183 > 100, healthy)
    # Beef (915 > 500, healthy)
    low_stock_names = [it["item_name"] for it in res["low_stock_items"]]
    assert "Roti bun (burger)" in low_stock_names

    roti = next(it for it in res["low_stock_items"] if "Roti bun" in it["item_name"])
    assert roti["deficit"] == 17.0  # 100 - 83 = 17


def test_supplier_resolver_and_po_generator():
    """Verify supplier resolver matches items and produces safe wa.me direct links."""
    catalog = {
        "SUPP-BAKERY": {
            "name": "Pabrik Roti Mas Joko",
            "phone": "0812-3456-7890",
            "items": ["Roti bun burger", "Roti tawar"],
        }
    }
    alias_table = {"roti bun (burger)": "roti bun burger"}
    resolver = SupplierResolver(supplier_catalog=catalog, alias_table=alias_table)

    supp = resolver.resolve_supplier_for_item("Roti bun (burger)")
    assert supp is not None
    assert supp["name"] == "Pabrik Roti Mas Joko"

    # Generate PO WhatsApp draft
    items_order = [{"item_name": "Roti bun (burger)", "order_qty": 50.0, "unit": "pcs"}]
    po = generate_po_whatsapp_draft(
        supplier_name=supp["name"],
        supplier_phone=supp["phone"],
        items_to_order=items_order,
        store_name="Kedai Burger Juara",
    )

    assert po["supplier_phone"] == "6281234567890"  # Normalized
    assert "https://wa.me/6281234567890?text=" in po["wa_url"]
    assert "Kedai Burger Juara" in po["po_text"]
    assert "50 pcs" in po["po_text"]
    assert po["status"] == "Draf diserahkan ke owner"
    assert po["is_long_url"] is False

