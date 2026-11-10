"""
app/services/inventory_pack/tools.py
Inventory Tool Pack: registers restock tools as certified BaseTools so the synthesizer
can compose them like any other pack. Domain logic lives in app/services/restock/;
this module only adapts it to the engine contract (schemas + metadata + risk tier).
"""

import io
import math
from typing import Any, Dict, List, Optional

import pandas as pd
from pydantic import BaseModel, Field

from app.core.tool_registry import (
    BaseTool,
    ExecutionContext,
    ToolCategory,
    ToolMetadata,
    ToolRegistry,
    ToolRiskTier,
)
from app.services.restock.inventory_filter import filter_low_stock_items
from app.services.restock.inventory_ingest import extract_current_inventory_status
from app.services.restock.po_generator import SupplierResolver, generate_po_whatsapp_draft


# =====================================================================
# Shared models
# =====================================================================

class StockItem(BaseModel):
    item_id: str
    item_name: str
    current_stock: float
    unit: str = "pcs"
    safety_stock: Optional[float] = None
    deficit: Optional[float] = None
    reorder_needed: Optional[bool] = None


# =====================================================================
# 1. inventory.ingest_stock_ledger
# =====================================================================

class IngestStockLedgerConfig(BaseModel):
    sku_column: Optional[str] = Field(None, description="Override auto-detected SKU/code column")
    name_column: Optional[str] = Field(None, description="Override auto-detected item name column")
    stock_column: Optional[str] = Field(None, description="Override auto-detected current stock column")
    time_column: Optional[str] = Field(None, description="Override auto-detected timestamp column")


class IngestStockLedgerInput(BaseModel):
    csv_content: Optional[str] = None


class StockSnapshot(BaseModel):
    items: List[StockItem]
    total_rows: int = Field(..., description="Raw rows read from the file")
    total_items: int = Field(..., description="Distinct items after aggregation")


DEFAULT_DEMO_STOCK_CSV = """Kode,Nama Bahan Baku,Stok Awal,Tambah Stok,Kurangi Stok,Stok Akhir,Jurnal,Sisa Modal,Update,Status,Keterangan
BHN-5,Beef,910.00 pcs,5.00 pcs,-,915.00 pcs,5000,4708650,2026-10-13 21:23:31,Aman,Pesanan #ORD-1791901372481
BHN-4,Saus tomat,420.00 ml,30.00 ml,-,450.00 ml,750,4703650,2026-10-13 21:23:31,Aman,Pesanan #ORD-1791901372481
BHN-2,Roti bun (burger),82.00 pcs,1.00 pcs,-,83.00 pcs,2000,4702900,2026-10-13 21:23:31,Aman,Pesanan #ORD-1791901372481
BHN-7,Keju slice,182.00 pcs,1.00 pcs,-,183.00 pcs,1000,4700900,2026-10-13 21:23:31,Aman,Pesanan #ORD-1791901372481
BHN-9,Paper bag (Size S),997.00 pcs,1.00 pcs,-,998.00 pcs,1300,4752400,2026-10-13 21:21:25,Aman,Pesanan #ORD-1791901262485
"""


class IngestStockLedgerTool(BaseTool):
    metadata = ToolMetadata(
        tool_type="inventory.ingest_stock_ledger",
        display_name="Baca Kartu Stok",
        description=(
            "Reads a stock list or stock mutation ledger (CSV) and returns the latest stock per item. "
            "Handles quantities with units ('910.00 pcs'), '-' placeholders, and Rupiah formats."
        ),
        category=ToolCategory.INGESTION,
        risk_tier=ToolRiskTier.NONE,
    )
    config_schema = IngestStockLedgerConfig
    input_schema = IngestStockLedgerInput
    output_schema = StockSnapshot

    async def execute(
        self, config: IngestStockLedgerConfig, inputs: IngestStockLedgerInput, context: ExecutionContext
    ) -> StockSnapshot:
        csv_text = inputs.csv_content or DEFAULT_DEMO_STOCK_CSV
        df = pd.read_csv(io.StringIO(csv_text))
        items = extract_current_inventory_status(
            df,
            sku_col=config.sku_column,
            name_col=config.name_column,
            stock_col=config.stock_column,
            time_col=config.time_column,
        )
        return StockSnapshot(
            items=[StockItem(**it) for it in items],
            total_rows=len(df),
            total_items=len(items),
        )


# =====================================================================
# 2. inventory.filter_low_stock
# =====================================================================

class FilterLowStockConfig(BaseModel):
    default_safety_stock: float = Field(100.0, ge=0, description="Threshold used when an item has no override")
    safety_stock_overrides: Dict[str, float] = Field(
        default_factory=dict, description="Per-item thresholds keyed by item_id or item_name"
    )


class FilterLowStockInput(BaseModel):
    snapshot: StockSnapshot


class LowStockReport(BaseModel):
    low_stock_items: List[StockItem]
    healthy_items: List[StockItem]
    total_rows: int
    flagged_items_count: int


class FilterLowStockTool(BaseTool):
    metadata = ToolMetadata(
        tool_type="inventory.filter_low_stock",
        display_name="Cek Stok di Bawah Batas Aman",
        description="Flags items whose current stock is at or below their safety stock threshold.",
        category=ToolCategory.ANALYSIS,
        risk_tier=ToolRiskTier.NONE,
    )
    config_schema = FilterLowStockConfig
    input_schema = FilterLowStockInput
    output_schema = LowStockReport

    def apply_business_config(self, raw_config: Dict[str, Any], business_config: Dict[str, Any]) -> Dict[str, Any]:
        cfg = dict(raw_config)
        if "default_safety_stock" in business_config:
            cfg["default_safety_stock"] = float(business_config["default_safety_stock"])
        return cfg

    async def execute(
        self, config: FilterLowStockConfig, inputs: FilterLowStockInput, context: ExecutionContext
    ) -> LowStockReport:
        result = filter_low_stock_items(
            [it.model_dump() for it in inputs.snapshot.items],
            default_safety_stock=config.default_safety_stock,
            safety_stock_overrides=config.safety_stock_overrides,
        )
        return LowStockReport(
            low_stock_items=[StockItem(**it) for it in result["low_stock_items"]],
            healthy_items=[StockItem(**it) for it in result["healthy_items"]],
            total_rows=result["total_items"],
            flagged_items_count=result["needs_reorder_count"],
        )


# =====================================================================
# 3. inventory.draft_supplier_po
# =====================================================================

class SupplierEntry(BaseModel):
    name: str
    phone: str
    items: List[str] = Field(default_factory=list)


class DraftSupplierPoConfig(BaseModel):
    store_name: str = "Toko UMKM Saya"
    suppliers: Dict[str, SupplierEntry] = Field(
        default_factory=lambda: {
            "SUPP-BAKERY": SupplierEntry(
                name="Pabrik Roti & Keju Berkah",
                phone="081234567890",
                items=["Roti bun (burger)", "Roti bun", "Keju slice", "Paper bag (Size S)"],
            ),
            "SUPP-MEAT": SupplierEntry(
                name="CV Sumber Daging & Saus",
                phone="081987654321",
                items=["Beef", "Daging sapi", "Saus tomat"],
            ),
        },
        description="supplier_id -> supplier",
    )
    alias_table: Dict[str, str] = Field(default_factory=dict, description="raw item name -> catalog item name")
    reorder_buffer_pct: float = Field(0.0, ge=0, le=200, description="Extra % ordered on top of the deficit")


class DraftSupplierPoInput(BaseModel):
    report: LowStockReport


class PoDraft(BaseModel):
    supplier_name: str
    supplier_phone: str
    po_text: str
    wa_url: str
    is_long_url: bool
    fallback_codeblock: Optional[str] = None
    item_count: int
    status: str


class PoDraftBundle(BaseModel):
    drafts: List[PoDraft]
    unassigned_items: List[StockItem] = Field(
        default_factory=list, description="Low-stock items with no matching supplier; owner must handle manually"
    )
    total_rows: int
    flagged_items_count: int


class DraftSupplierPoTool(BaseTool):
    # INTERNAL, not EXTERNAL: this tool only produces drafts and wa.me links.
    # Kirata never sends the message; the owner taps the link and sends it from their own WhatsApp,
    # so the human is the dispatch step. A future tool that sends automatically must be EXTERNAL.
    metadata = ToolMetadata(
        tool_type="inventory.draft_supplier_po",
        display_name="Siapkan Draf Pesanan Supplier",
        description=(
            "Groups low-stock items by supplier and prepares WhatsApp order drafts with wa.me links. "
            "Does not send anything; the owner reviews and sends manually."
        ),
        category=ToolCategory.EXPORT,
        risk_tier=ToolRiskTier.INTERNAL,
    )
    config_schema = DraftSupplierPoConfig
    input_schema = DraftSupplierPoInput
    output_schema = PoDraftBundle

    def apply_business_config(self, raw_config: Dict[str, Any], business_config: Dict[str, Any]) -> Dict[str, Any]:
        cfg = dict(raw_config)
        if "reorder_buffer_pct" in business_config:
            cfg["reorder_buffer_pct"] = float(business_config["reorder_buffer_pct"])
        if "store_name" in business_config:
            cfg["store_name"] = str(business_config["store_name"])
        return cfg

    async def execute(
        self, config: DraftSupplierPoConfig, inputs: DraftSupplierPoInput, context: ExecutionContext
    ) -> PoDraftBundle:
        resolver = SupplierResolver(
            supplier_catalog={sid: s.model_dump() for sid, s in config.suppliers.items()},
            alias_table={k.strip().lower(): v for k, v in config.alias_table.items()},
        )

        grouped: Dict[str, Dict[str, Any]] = {}
        unassigned: List[StockItem] = []
        for item in inputs.report.low_stock_items:
            supplier = resolver.resolve_supplier_for_item(item.item_name)
            if not supplier:
                unassigned.append(item)
                continue
            deficit = item.deficit or 0.0
            qty = math.ceil(deficit * (1 + config.reorder_buffer_pct / 100.0))
            if qty <= 0:
                continue
            bucket = grouped.setdefault(supplier["supplier_id"], {"supplier": supplier, "items": []})
            bucket["items"].append({"item_name": item.item_name, "order_qty": float(qty), "unit": item.unit})

        drafts = [
            PoDraft(**generate_po_whatsapp_draft(
                supplier_name=b["supplier"]["name"],
                supplier_phone=b["supplier"]["phone"],
                items_to_order=b["items"],
                store_name=config.store_name,
            ))
            for b in grouped.values()
        ]
        return PoDraftBundle(
            drafts=drafts,
            unassigned_items=unassigned,
            total_rows=inputs.report.total_rows,
            flagged_items_count=inputs.report.flagged_items_count,
        )


# =====================================================================
# Registration
# =====================================================================

from app.core.governance_tools import register_governance_tools


def register_inventory_tools() -> None:
    register_governance_tools()
    for tool in (IngestStockLedgerTool(), FilterLowStockTool(), DraftSupplierPoTool()):
        ToolRegistry.register(tool)

