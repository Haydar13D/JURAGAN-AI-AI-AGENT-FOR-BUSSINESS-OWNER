"""
app/services/retail_pricing/tools.py
Executable Tool implementations for the Used-iPhone Retail Pricing Vertical.
All tools inherit from BaseTool and register with ToolRegistry.
"""

import os
import csv
import io
import math
import json
from datetime import datetime
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field

from app.core.tool_registry import BaseTool, ToolMetadata, ToolCategory, ExecutionContext, ToolRegistry
from app.services.retail_pricing.schemas import (
    BatchNormalizedProducts,
    NormalizedProduct,
    BatchMatchedReferences,
    MatchedReferenceProduct,
    MarketReference,
    BatchPricedProducts,
    PricedProduct,
    BatchEvaluatedCatalog,
    EvaluatedProduct,
    ApprovedCatalogPayload,
    ExportResult
)


# =====================================================================
# 1. TOOL: UPLOAD / INGEST VENDOR LIST
# =====================================================================

class UploadVendorListConfig(BaseModel):
    allowed_formats: List[str] = Field(default_factory=lambda: ["csv"])


class UploadVendorListInput(BaseModel):
    raw_csv_path: Optional[str] = "data/fixtures/vendor_raw_samples.csv"
    csv_content: Optional[str] = None


class UploadVendorListOutput(BaseModel):
    file_path: str
    csv_content: str
    row_count: int


class UploadVendorListTool(BaseTool):
    metadata = ToolMetadata(
        tool_type="retail.upload_vendor_list",
        version="1.0.0",
        display_name="Upload Vendor List",
        description="Reads incoming vendor price lists in CSV format.",
        category=ToolCategory.INGESTION,
        is_side_effect=False,
        requires_action_approval=False
    )
    config_schema = UploadVendorListConfig
    input_schema = UploadVendorListInput
    output_schema = UploadVendorListOutput

    async def execute(self, config: UploadVendorListConfig, inputs: UploadVendorListInput, context: ExecutionContext) -> UploadVendorListOutput:
        if inputs.csv_content:
            content = inputs.csv_content
            file_path = "memory://vendor_upload.csv"
        else:
            path = inputs.raw_csv_path or "data/fixtures/vendor_raw_samples.csv"
            if not os.path.exists(path):
                raise FileNotFoundError(f"Vendor CSV fixture not found at {path}")
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            file_path = path

        reader = csv.reader(io.StringIO(content))
        rows = list(reader)
        row_count = max(0, len(rows) - 1)  # exclude header

        return UploadVendorListOutput(
            file_path=file_path,
            csv_content=content,
            row_count=row_count
        )


# =====================================================================
# 2. TOOL: EXTRACT AND NORMALIZE
# =====================================================================

class ExtractAndNormalizeConfig(BaseModel):
    default_currency: str = "IDR"
    clean_model_names: bool = True


class ExtractAndNormalizeInput(BaseModel):
    raw_data: Optional[UploadVendorListOutput] = None
    csv_content: Optional[str] = None


class ExtractAndNormalizeTool(BaseTool):
    metadata = ToolMetadata(
        tool_type="retail.extract_and_normalize",
        version="1.0.0",
        display_name="Extract & Normalize Specs",
        description="Parses messy vendor rows, standardizes model names, storage, grade, and distribution.",
        category=ToolCategory.TRANSFORMATION,
        is_side_effect=False,
        requires_action_approval=False
    )
    config_schema = ExtractAndNormalizeConfig
    input_schema = ExtractAndNormalizeInput
    output_schema = BatchNormalizedProducts

    async def execute(self, config: ExtractAndNormalizeConfig, inputs: ExtractAndNormalizeInput, context: ExecutionContext) -> BatchNormalizedProducts:
        content = inputs.csv_content or (inputs.raw_data.csv_content if inputs.raw_data else "")
        if not content:
            # Fallback to fixture
            with open("data/fixtures/vendor_raw_samples.csv", "r", encoding="utf-8") as f:
                content = f.read()

        reader = csv.DictReader(io.StringIO(content))
        normalized_items: List[NormalizedProduct] = []
        quarantined = 0

        for idx, row in enumerate(reader):
            item_id = f"item_{idx + 1:03d}"
            raw_model = row.get("model", "").strip()
            raw_storage = row.get("storage_gb", "").strip().lower()
            raw_cost = row.get("cost", "").strip()
            raw_battery = row.get("battery_health", "").strip()
            raw_dist = row.get("distribution_label", "").strip()
            raw_cond = row.get("unit_condition", "").strip()
            raw_warranty = row.get("warranty", "").strip()

            # Normalization helpers
            norm_model = self._normalize_model(raw_model)
            norm_storage = self._normalize_storage(raw_storage)
            norm_dist = self._normalize_distribution(raw_dist)
            norm_grade = self._normalize_grade(raw_cond)
            battery_val = self._parse_battery(raw_battery)
            cost_val, is_cost_valid = self._parse_cost(raw_cost)

            is_valid = True
            quarantine_reason = None

            if not norm_model or norm_model == "Unknown iPhone":
                is_valid = False
                quarantine_reason = "Model tidak dapat diidentifikasi."
            elif norm_storage == 0:
                is_valid = False
                quarantine_reason = "Kapasitas storage kosong atau tidak valid."
            elif not is_cost_valid or cost_val <= 0:
                is_valid = False
                quarantine_reason = f"Harga modal vendor tidak valid ({raw_cost})."

            if not is_valid:
                quarantined += 1

            normalized_items.append(NormalizedProduct(
                item_id=item_id,
                vendor_id=row.get("vendor_id", "UNKNOWN"),
                model=norm_model,
                storage_gb=norm_storage,
                color=row.get("color", "Standard").strip(),
                condition_grade=norm_grade,
                distribution_label=norm_dist,
                warranty_status="Active" if "aktif" in raw_warranty.lower() else "Expired",
                battery_health=battery_val,
                cost=cost_val,
                stock=int(row.get("stock", 1)) if row.get("stock", "1").isdigit() else 1,
                currency=row.get("currency", config.default_currency).strip(),
                is_valid_row=is_valid,
                quarantine_reason=quarantine_reason
            ))

        return BatchNormalizedProducts(
            items=normalized_items,
            total_rows=len(normalized_items),
            quarantined_count=quarantined
        )

    def _normalize_model(self, raw: str) -> str:
        s = raw.lower().replace("ip ", "iphone ").replace("iph ", "iphone ")
        if "15 pro max" in s: return "iPhone 15 Pro Max"
        if "15 pro" in s: return "iPhone 15 Pro"
        if "15 plus" in s: return "iPhone 15 Plus"
        if "15" in s: return "iPhone 15"
        if "14 pro max" in s: return "iPhone 14 Pro Max"
        if "14 pro" in s: return "iPhone 14 Pro"
        if "14 plus" in s: return "iPhone 14 Plus"
        if "14" in s: return "iPhone 14"
        if "13 pro max" in s: return "iPhone 13 Pro Max"
        if "13 pro" in s: return "iPhone 13 Pro"
        if "13 mini" in s: return "iPhone 13 mini"
        if "13" in s: return "iPhone 13"
        if "12 pro max" in s: return "iPhone 12 Pro Max"
        if "12 pro" in s: return "iPhone 12 Pro"
        if "12 mini" in s: return "iPhone 12 mini"
        if "12" in s: return "iPhone 12"
        if "11 pro max" in s: return "iPhone 11 Pro Max"
        if "11 pro" in s: return "iPhone 11 Pro"
        if "11" in s: return "iPhone 11"
        return "Unknown iPhone"

    def _normalize_storage(self, raw: str) -> int:
        clean = raw.replace("gb", "").replace("g", "").strip()
        try:
            return int(clean)
        except ValueError:
            return 0

    def _normalize_distribution(self, raw: str) -> str:
        s = raw.lower()
        if "ibox" in s or "resmi" in s:
            return "iBox"
        if "inter" in s:
            return "Inter"
        return "Unknown"

    def _normalize_grade(self, raw: str) -> str:
        s = raw.lower()
        if "99%" in s or "like new" in s or "grade a" in s:
            return "A"
        if "95%" in s or "mulus" in s:
            return "A"
        if "lecet" in s or "dent" in s or "grade b" in s:
            return "B"
        return "B"

    def _parse_battery(self, raw: str) -> Optional[int]:
        clean = raw.replace("%", "").strip()
        try:
            val = int(clean)
            return val if 0 < val <= 100 else None
        except ValueError:
            return None

    def _parse_cost(self, raw: str) -> Tuple[int, bool]:
        clean = raw.replace("rp", "").replace(".", "").replace(",", "").strip()
        try:
            cost = int(clean)
            return cost, cost > 0
        except ValueError:
            return 0, False


# =====================================================================
# 3. TOOL: VALIDATE ATTRIBUTES
# =====================================================================

class ValidateAttributesConfig(BaseModel):
    required_fields: List[str] = Field(default_factory=lambda: ["model", "storage_gb", "cost", "unit_condition"])


class ValidateAttributesInput(BaseModel):
    items: Optional[BatchNormalizedProducts] = None


class ValidateAttributesTool(BaseTool):
    metadata = ToolMetadata(
        tool_type="retail.validate_attributes",
        version="1.0.0",
        display_name="Validate Critical Attributes",
        description="Flags missing critical fields as quarantined rows.",
        category=ToolCategory.TRANSFORMATION,
        is_side_effect=False,
        requires_action_approval=False
    )
    config_schema = ValidateAttributesConfig
    input_schema = ValidateAttributesInput
    output_schema = BatchNormalizedProducts

    async def execute(self, config: ValidateAttributesConfig, inputs: ValidateAttributesInput, context: ExecutionContext) -> BatchNormalizedProducts:
        if not inputs.items:
            raise ValueError("No input products provided to validate_attributes")
        return inputs.items


# =====================================================================
# 4. TOOL: MATCH MARKET REFERENCES
# =====================================================================

class MatchMarketReferencesConfig(BaseModel):
    max_data_age_days: int = 14
    reference_dataset_path: str = "data/fixtures/market_references.json"


class MatchMarketReferencesInput(BaseModel):
    items: Optional[BatchNormalizedProducts] = None


class MatchMarketReferencesTool(BaseTool):
    metadata = ToolMetadata(
        tool_type="retail.match_market_references",
        version="1.0.0",
        display_name="Match Market References",
        description="Matches normalized iPhone listings with curated comparable reference market data.",
        category=ToolCategory.ANALYSIS,
        is_side_effect=False,
        requires_action_approval=False
    )
    config_schema = MatchMarketReferencesConfig
    input_schema = MatchMarketReferencesInput
    output_schema = BatchMatchedReferences

    async def execute(self, config: MatchMarketReferencesConfig, inputs: MatchMarketReferencesInput, context: ExecutionContext) -> BatchMatchedReferences:
        if not inputs.items:
            raise ValueError("No items provided to match_market_references")

        ref_path = config.reference_dataset_path
        market_refs: List[MarketReference] = []
        if os.path.exists(ref_path):
            with open(ref_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                market_refs = [MarketReference.model_validate(r) for r in data]

        matched_list: List[MatchedReferenceProduct] = []
        now = datetime.now()

        for item in inputs.items.items:
            if not item.is_valid_row:
                matched_list.append(MatchedReferenceProduct(item=item, has_market_reference=False))
                continue

            # Find matching reference by model + storage + distribution + grade
            match = next((
                r for r in market_refs
                if r.model == item.model and r.storage_gb == item.storage_gb and r.distribution_label == item.distribution_label
            ), None)

            # Fallback matching by model + storage only
            if not match:
                match = next((
                    r for r in market_refs
                    if r.model == item.model and r.storage_gb == item.storage_gb
                ), None)

            if match:
                # Check data age
                is_stale = False
                try:
                    obs_dt = datetime.fromisoformat(match.observed_at.replace("Z", "+00:00")).replace(tzinfo=None)
                    age_days = (now - obs_dt).days
                    if age_days > config.max_data_age_days or match.confidence < 0.60:
                        is_stale = True
                except Exception:
                    is_stale = False

                matched_list.append(MatchedReferenceProduct(
                    item=item,
                    has_market_reference=True,
                    market_price=match.observed_price,
                    market_source=match.source,
                    market_confidence=match.confidence,
                    market_observed_at=match.observed_at,
                    is_stale_market_data=is_stale
                ))
            else:
                matched_list.append(MatchedReferenceProduct(
                    item=item,
                    has_market_reference=False
                ))

        return BatchMatchedReferences(items=matched_list)


# =====================================================================
# 5. TOOL: CALCULATE PRICING (DETERMINISTIC FORMULA)
# =====================================================================

class CalculatePricingConfig(BaseModel):
    target_gross_margin: float = Field(default=0.15, ge=0.01, le=0.90)
    overhead_fee: int = Field(default=50000, ge=0)
    rounding_increment: int = Field(default=50000, ge=1000)


class CalculatePricingInput(BaseModel):
    items: Optional[BatchMatchedReferences] = None


class CalculatePricingTool(BaseTool):
    metadata = ToolMetadata(
        tool_type="retail.calculate_pricing",
        version="1.0.0",
        display_name="Calculate Target Margin Price",
        description="Calculates deterministic selling prices ensuring target gross margin: min_price = total_cost / (1 - m).",
        category=ToolCategory.ANALYSIS,
        is_side_effect=False,
        requires_action_approval=False
    )
    config_schema = CalculatePricingConfig
    input_schema = CalculatePricingInput
    output_schema = BatchPricedProducts

    async def execute(self, config: CalculatePricingConfig, inputs: CalculatePricingInput, context: ExecutionContext) -> BatchPricedProducts:
        if not inputs.items:
            raise ValueError("No matched items provided to calculate_pricing")

        priced_items: List[PricedProduct] = []

        for matched in inputs.items.items:
            item = matched.item
            if not item.is_valid_row:
                priced_items.append(PricedProduct(
                    item=item,
                    market_ref=matched,
                    total_cost=0,
                    target_gross_margin=config.target_gross_margin,
                    raw_min_price=0.0,
                    recommended_selling_price=0,
                    expected_margin=0.0,
                    markup=0.0
                ))
                continue

            total_cost = item.cost + config.overhead_fee
            # PRD Formula: min_price = total_cost / (1 - m)
            raw_min = total_cost / (1.0 - config.target_gross_margin)
            # Round up to increment
            rec_price = int(math.ceil(raw_min / config.rounding_increment) * config.rounding_increment)
            actual_margin = (rec_price - total_cost) / rec_price if rec_price > 0 else 0.0
            markup = (rec_price - total_cost) / total_cost if total_cost > 0 else 0.0

            priced_items.append(PricedProduct(
                item=item,
                market_ref=matched,
                total_cost=total_cost,
                target_gross_margin=config.target_gross_margin,
                raw_min_price=raw_min,
                recommended_selling_price=rec_price,
                expected_margin=actual_margin,
                markup=markup
            ))

        return BatchPricedProducts(items=priced_items)


# =====================================================================
# 6. TOOL: COMPARE TO MARKET
# =====================================================================

class CompareToMarketConfig(BaseModel):
    max_premium_ratio: float = Field(default=0.05, description="Max allowed selling price above market price without review flag")


class CompareToMarketInput(BaseModel):
    items: Optional[BatchPricedProducts] = None


class CompareToMarketTool(BaseTool):
    metadata = ToolMetadata(
        tool_type="retail.compare_to_market",
        version="1.0.0",
        display_name="Compare with Market Bounds",
        description="Compares calculated selling price against market reference price and flags premiums.",
        category=ToolCategory.ANALYSIS,
        is_side_effect=False,
        requires_action_approval=False
    )
    config_schema = CompareToMarketConfig
    input_schema = CompareToMarketInput
    output_schema = BatchEvaluatedCatalog

    async def execute(self, config: CompareToMarketConfig, inputs: CompareToMarketInput, context: ExecutionContext) -> BatchEvaluatedCatalog:
        if not inputs.items:
            raise ValueError("No priced items provided to compare_to_market")

        eval_items: List[EvaluatedProduct] = []

        for p in inputs.items.items:
            flags: List[str] = []
            diff_amount = None
            premium_ratio = None

            if not p.item.is_valid_row:
                flags.append("QUARANTINED")
                eval_items.append(EvaluatedProduct(
                    pricing=p,
                    review_flags=flags,
                    final_status="NEEDS_REVIEW"
                ))
                continue

            ref = p.market_ref
            if ref and ref.has_market_reference and ref.market_price:
                diff_amount = p.recommended_selling_price - ref.market_price
                premium_ratio = diff_amount / ref.market_price

                if premium_ratio > config.max_premium_ratio:
                    flags.append("ABOVE_MARKET")
            else:
                flags.append("MISSING_MARKET_REF")

            eval_items.append(EvaluatedProduct(
                pricing=p,
                market_diff_amount=diff_amount,
                market_premium_ratio=premium_ratio,
                review_flags=flags,
                final_status="NEEDS_REVIEW" if flags else "APPROVED_CANDIDATE"
            ))

        return BatchEvaluatedCatalog(
            items=eval_items,
            total_items=len(eval_items),
            clean_items_count=sum(1 for i in eval_items if not i.review_flags),
            flagged_items_count=sum(1 for i in eval_items if i.review_flags)
        )


# =====================================================================
# 7. TOOL: CONDITIONAL REVIEW FLAG
# =====================================================================

class ConditionalReviewFlagConfig(BaseModel):
    flag_low_battery_under: int = Field(default=80)
    flag_missing_reference: bool = Field(default=True)


class ConditionalReviewFlagInput(BaseModel):
    items: Optional[BatchEvaluatedCatalog] = None


class ConditionalReviewFlagTool(BaseTool):
    metadata = ToolMetadata(
        tool_type="retail.conditional_review_flag",
        version="1.0.0",
        display_name="Flag Exceptions for Review",
        description="Flags condition exceptions such as low battery health or stale market reference.",
        category=ToolCategory.ANALYSIS,
        is_side_effect=False,
        requires_action_approval=False
    )
    config_schema = ConditionalReviewFlagConfig
    input_schema = ConditionalReviewFlagInput
    output_schema = BatchEvaluatedCatalog

    async def execute(self, config: ConditionalReviewFlagConfig, inputs: ConditionalReviewFlagInput, context: ExecutionContext) -> BatchEvaluatedCatalog:
        if not inputs.items:
            raise ValueError("No catalog items provided to conditional_review_flag")

        for eval_p in inputs.items.items:
            item = eval_p.pricing.item
            if item.battery_health and item.battery_health < config.flag_low_battery_under:
                if "LOW_BATTERY" not in eval_p.review_flags:
                    eval_p.review_flags.append(f"LOW_BATTERY_{item.battery_health}%")

            ref = eval_p.pricing.market_ref
            if ref and ref.is_stale_market_data:
                if "STALE_MARKET_REF" not in eval_p.review_flags:
                    eval_p.review_flags.append("STALE_MARKET_REF")

            if eval_p.review_flags:
                eval_p.final_status = "NEEDS_REVIEW"

        inputs.items.clean_items_count = sum(1 for i in inputs.items.items if not i.review_flags)
        inputs.items.flagged_items_count = sum(1 for i in inputs.items.items if i.review_flags)
        return inputs.items


# =====================================================================
# 8. TOOL: OWNER ACTION APPROVAL (GOVERNANCE GATE)
# =====================================================================

class OwnerActionApprovalConfig(BaseModel):
    action_type: str = "CATALOG_PUBLISH_OR_EXPORT"
    require_preview: bool = True


class OwnerActionApprovalInput(BaseModel):
    catalog: Optional[Any] = None
    campaign_payload: Optional[Any] = None
    payload: Optional[Any] = None


class OwnerActionApprovalTool(BaseTool):
    metadata = ToolMetadata(
        tool_type="governance.action_approval",
        version="1.0.0",
        display_name="Owner Action Approval",
        description="Human-in-the-loop safety gate. Pauses execution for explicit owner approval before live export or external publication.",
        category=ToolCategory.GOVERNANCE,
        is_side_effect=False,
        requires_action_approval=True
    )
    config_schema = OwnerActionApprovalConfig
    input_schema = OwnerActionApprovalInput
    output_schema = BaseModel

    async def execute(self, config: OwnerActionApprovalConfig, inputs: OwnerActionApprovalInput, context: ExecutionContext) -> Any:
        payload = inputs.catalog or inputs.campaign_payload or inputs.payload
        if not payload:
            raise ValueError("No payload provided to owner_action_approval")

        # In dry run mode, allow simulated approval
        if context.is_dry_run:
            if isinstance(payload, BatchEvaluatedCatalog):
                return ApprovedCatalogPayload(
                    catalog=payload,
                    is_approved=True,
                    approved_by="dry_run_simulator",
                    approval_timestamp=datetime.now().isoformat()
                )
            return payload

        # In live run mode, verify actor approval decision from context
        approved = context.extra_meta.get("action_approved", False)
        if not approved:
            # The workflow runtime engine will intercept this and set status WAITING_APPROVAL
            raise PermissionError(f"ACTION_APPROVAL_REQUIRED: Human approval required before {config.action_type}.")

        if isinstance(payload, BatchEvaluatedCatalog):
            return ApprovedCatalogPayload(
                catalog=payload,
                is_approved=True,
                approved_by=context.actor_id or "owner",
                approval_timestamp=datetime.now().isoformat()
            )
        return payload


# =====================================================================
# 9. TOOL: EXPORT CATALOG
# =====================================================================

class ExportCatalogConfig(BaseModel):
    export_format: str = "csv"


class ExportCatalogInput(BaseModel):
    payload: Optional[ApprovedCatalogPayload] = None


class ExportCatalogTool(BaseTool):
    metadata = ToolMetadata(
        tool_type="retail.export_catalog",
        version="1.0.0",
        display_name="Export Final Catalog",
        description="Exports approved retail pricing catalog into customer-ready CSV/table format.",
        category=ToolCategory.EXPORT,
        is_side_effect=True,
        requires_action_approval=False
    )
    config_schema = ExportCatalogConfig
    input_schema = ExportCatalogInput
    output_schema = ExportResult

    async def execute(self, config: ExportCatalogConfig, inputs: ExportCatalogInput, context: ExecutionContext) -> ExportResult:
        if not inputs.payload or not inputs.payload.catalog:
            raise ValueError("No approved payload provided to export_catalog")

        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow([
            "Item ID", "Model", "Storage", "Grade", "Distribution", "Battery",
            "Vendor Cost", "Rec Selling Price", "Expected Margin", "Status", "Review Flags"
        ])

        exported = 0
        for ep in inputs.payload.catalog.items:
            p = ep.pricing
            item = p.item
            if item.is_valid_row:
                writer.writerow([
                    item.item_id,
                    item.model,
                    f"{item.storage_gb}GB",
                    item.condition_grade,
                    item.distribution_label,
                    f"{item.battery_health}%" if item.battery_health else "-",
                    f"Rp{p.total_cost:,}",
                    f"Rp{p.recommended_selling_price:,}",
                    f"{p.expected_margin * 100:.1f}%",
                    ep.final_status,
                    ", ".join(ep.review_flags) if ep.review_flags else "CLEAN"
                ])
                exported += 1

        csv_text = out.getvalue()
        return ExportResult(
            file_format=config.export_format,
            exported_rows_count=exported,
            export_csv_content=csv_text,
            summary=f"Berhasil mengekspor {exported} produk iPhone bekas ke format {config.export_format}."
        )


# =====================================================================
# AUTO-REGISTRATION HELPER
# =====================================================================

def register_retail_tools() -> None:
    """Registers all certified retail tools with ToolRegistry singleton."""
    tools = [
        UploadVendorListTool(),
        ExtractAndNormalizeTool(),
        ValidateAttributesTool(),
        MatchMarketReferencesTool(),
        CalculatePricingTool(),
        CompareToMarketTool(),
        ConditionalReviewFlagTool(),
        OwnerActionApprovalTool(),
        ExportCatalogTool()
    ]
    for t in tools:
        ToolRegistry.register(t)
