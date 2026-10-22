"""
app/services/template_catalog.py
Template Gallery catalog for Kirata AI Workflow Builder (PRD v2.0 §5.1 / FR-01).

Each entry describes the business outcome, required inputs, tools used, limitations,
and expected output so an owner can choose a validated workflow before configuring it.
Only templates with status AVAILABLE have a certified canonical definition and can be instantiated.
"""

import json
import os
from typing import Any, Dict, List, Optional

TEMPLATE_DEFINITION_FILES: Dict[str, str] = {
    "tpl_used_iphone_pricing_v1": "data/fixtures/retail_pricing_template.json",
    "tpl_fnb_sales_to_marketing_v1": "data/fixtures/fnb_marketing_template.json",
}

TEMPLATE_CATALOG: List[Dict[str, Any]] = [
    {
        "id": "tpl_used_iphone_pricing_v1",
        "name": "Retail Pricing — iPhone Bekas",
        "industry": "Retail Gadget",
        "icon": "📱",
        "version": "1.0.0",
        "status": "AVAILABLE",
        "business_outcome": (
            "Ubah daftar harga vendor yang berantakan menjadi katalog harga jual "
            "dengan margin terjaga, lengkap dengan flag unit yang perlu dicek manual."
        ),
        "required_inputs": [
            "File CSV daftar vendor (model, storage, kondisi, distribusi, garansi, baterai, modal)",
            "Target gross margin (%)",
            "Biaya overhead per unit (Rp)",
            "Kelipatan pembulatan harga (Rp)",
        ],
        "expected_output": "CSV katalog harga jual + status CLEAN / NEEDS_REVIEW per unit, hanya setelah Anda setujui.",
        "limitations": [
            "Referensi harga pasar memakai dataset terkurasi sintetis, bukan harga live marketplace.",
            "Tidak memverifikasi legalitas / status impor unit.",
            "Hanya mendukung upload CSV (PDF/teks menyusul).",
        ],
        "onboarding_fields": [
            {"key": "target_gross_margin_pct", "label": "Target gross margin (%)", "type": "number",
             "default": 15, "min": 1, "max": 90, "node_id": "calculate_pricing",
             "config_key": "target_gross_margin", "transform": "pct_to_ratio",
             "help": "Margin = (harga jual − total biaya) / harga jual. Bukan markup."},
            {"key": "overhead_fee", "label": "Biaya overhead per unit (Rp)", "type": "number",
             "default": 50000, "min": 0, "node_id": "calculate_pricing", "config_key": "overhead_fee",
             "help": "Packing, ongkir masuk, QC, dll."},
            {"key": "rounding_increment", "label": "Pembulatan harga ke atas (Rp)", "type": "number",
             "default": 50000, "min": 1000, "node_id": "calculate_pricing", "config_key": "rounding_increment",
             "help": "Contoh 50.000 → Rp5.352.941 jadi Rp5.400.000."},
            {"key": "flag_low_battery_under", "label": "Flag review jika baterai di bawah (%)", "type": "number",
             "default": 80, "min": 50, "max": 100, "node_id": "conditional_review_flag",
             "config_key": "flag_low_battery_under", "help": "Unit di bawah ambang ini wajib dicek manual."},
            {"key": "max_premium_pct", "label": "Maks. selisih di atas harga pasar (%)", "type": "number",
             "default": 5, "min": 0, "max": 100, "node_id": "compare_to_market",
             "config_key": "max_premium_ratio", "transform": "pct_to_ratio",
             "help": "Di atas ini ditandai ABOVE_MARKET (harga tidak diturunkan otomatis)."},
        ],
    },
    {
        "id": "tpl_fnb_sales_to_marketing_v1",
        "name": "F&B Sales-to-Marketing",
        "industry": "F&B / Coffee Shop",
        "icon": "☕",
        "version": "1.0.0",
        "status": "AVAILABLE",
        "business_outcome": (
            "Deteksi penurunan penjualan produk menu dari data POS kasir, susun kampanye promo otomatis, "
            "buat flyer promosi Instagram, dan minta persetujuan owner sebelum dipublikasikan."
        ),
        "required_inputs": [
            "Data transaksi POS 14 hari terakhir (CSV / database kasir)",
            "Ambang batas penurunan volume penjualan (%)",
            "Diskon promosi yang diusulkan (%)",
            "Durasi kampanye promo (hari)"
        ],
        "expected_output": "Aset flyer promosi 1080x1080 + caption Instagram siap tayang (publikasi terkunci hingga disetujui owner).",
        "limitations": [
            "Publikasi default berjalan dalam mode Sandbox / Simulasi aman.",
            "Memerlukan minimal 10 transaksi historis produk agar sinyal anomali bermakna.",
            "Deteksi difokuskan pada produk dengan margin sehat (SHARP_DROP)."
        ],
        "onboarding_fields": [
            {"key": "min_drop_percentage", "label": "Batas penurunan penjualan pemicu promo (%)", "type": "number",
             "default": 25, "min": 10, "max": 80, "node_id": "detect_opportunities",
             "config_key": "min_drop_percentage",
             "help": "Promo hanya dibuat jika penjualan produk turun minimal sebesar persentase ini."},
            {"key": "default_discount_pct", "label": "Diskon promosi yang diusulkan (%)", "type": "number",
             "default": 15, "min": 5, "max": 50, "node_id": "plan_campaign",
             "config_key": "default_discount_pct",
             "help": "Diskon yang ditampilkan di flyer dan caption promo."},
            {"key": "promo_duration_days", "label": "Durasi promo aktif (hari)", "type": "number",
             "default": 7, "min": 1, "max": 30, "node_id": "plan_campaign",
             "config_key": "promo_duration_days",
             "help": "Masa berlaku promo yang tertulis di caption."},
            {"key": "target_audience", "label": "Target audiens promo", "type": "text",
             "default": "Pekerja remote & pelanggan setia jam santai", "node_id": "plan_campaign",
             "config_key": "target_audience",
             "help": "Konteks segmentasi audiens untuk narasi promosi."}
        ],
    },
    {
        "id": "tpl_fashion_cataloging",
        "name": "Fashion Cataloging",
        "industry": "Fashion Retail",
        "icon": "👗",
        "version": "-",
        "status": "ROADMAP",
        "business_outcome": "Normalisasi stok supplier fashion (ukuran, warna, bahan) menjadi katalog siap jual.",
        "required_inputs": ["Daftar stok supplier"],
        "expected_output": "Katalog produk terstruktur.",
        "limitations": ["Belum ada definisi workflow tersertifikasi."],
        "onboarding_fields": [],
    },
    {
        "id": "tpl_inventory_alerts",
        "name": "Inventory Alerts",
        "industry": "Multi-industri",
        "icon": "📦",
        "version": "-",
        "status": "ROADMAP",
        "business_outcome": "Peringatan stok menipis / stok mati berdasarkan data penjualan.",
        "required_inputs": ["Data stok", "Data penjualan"],
        "expected_output": "Daftar SKU yang perlu restock / clearance.",
        "limitations": ["Belum ada definisi workflow tersertifikasi."],
        "onboarding_fields": [],
    },
]


def list_catalog() -> List[Dict[str, Any]]:
    return [enrich_entry(dict(e)) for e in TEMPLATE_CATALOG]


def get_catalog_entry(template_id: str) -> Optional[Dict[str, Any]]:
    for e in TEMPLATE_CATALOG:
        if e["id"] == template_id:
            return enrich_entry(dict(e))
    return None


def load_template_definition(template_id: str) -> Optional[Dict[str, Any]]:
    path = TEMPLATE_DEFINITION_FILES.get(template_id)
    if not path or not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def enrich_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Attach the tool list derived from the canonical definition (single source of truth)."""
    definition = load_template_definition(entry["id"])
    if definition:
        entry["tools"] = [
            {"node_id": n["id"], "tool_type": n["tool_type"], "display_name": n.get("display_name") or n["id"]}
            for n in definition.get("nodes", [])
        ]
        entry["node_count"] = len(definition.get("nodes", []))
    else:
        entry["tools"] = []
        entry["node_count"] = 0
    return entry
