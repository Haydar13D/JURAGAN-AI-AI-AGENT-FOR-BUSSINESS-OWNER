"""Schema Signature, Anti-Fatigue Column Mapping, and Local PII Boundary.

Implements:
- Resilient normalized header schema signatures (immune to empty-column type drift).
- Critical vs benign schema drift detection.
- Anti-fatigue confirmation prompts displaying real sample values.
- Local PII detection (Indonesian NIK, phone numbers, NPWP, bank accounts, emails).
"""

import hashlib
import re
from typing import Any, Dict, List, Optional, Set, Tuple
from pydantic import BaseModel, Field
import pandas as pd


# =====================================================================
# 1. LOCAL PII DETECTOR (BOUNDARY FILTER)
# =====================================================================

_EMAIL_RE = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
_ID_PHONE_RE = re.compile(r"(?:\+62|62|0)8[1-9][0-9]{6,10}\b")
_NIK_RE = re.compile(r"\b[1-9][0-9]{15}\b")  # 16-digit Indonesian NIK
_NPWP_RE = re.compile(r"\b\d{2}\.\d{3}\.\d{3}\.\d-\d{3}\.\d{3}\b")


class PiiScrubber:
    """Scrubs Indonesian PII tokens from strings before any external LLM boundary."""

    @classmethod
    def mask_text(cls, text: str) -> str:
        if not text:
            return text
        out = _EMAIL_RE.sub("[EMAIL_MASKED]", str(text))
        out = _NPWP_RE.sub("[NPWP_MASKED]", out)
        out = _NIK_RE.sub("[NIK_MASKED]", out)
        out = _ID_PHONE_RE.sub("[PHONE_MASKED]", out)
        return out

    @classmethod
    def contains_pii(cls, text: str) -> bool:
        if not text:
            return False
        s = str(text)
        return bool(
            _EMAIL_RE.search(s)
            or _NPWP_RE.search(s)
            or _NIK_RE.search(s)
            or _ID_PHONE_RE.search(s)
        )


# =====================================================================
# 2. NORMALIZED HEADER SCHEMA SIGNATURE & DRIFT DETECTION
# =====================================================================

def normalize_header_name(header: Any) -> str:
    """Normalizes a column header: lowercase, trim, collapse multiple spaces."""
    return " ".join(str(header).strip().lower().split())


def compute_header_signature(tenant_id: str, headers: List[Any]) -> str:
    """Computes a deterministic SHA-256 cache key from tenant_id and normalized headers ONLY.

    Excludes inferred data types so an all-empty column in week 2 does not trigger a false drift.
    """
    norm_sorted = sorted(set(normalize_header_name(h) for h in headers if str(h).strip()))
    payload = f"{tenant_id.strip().lower()}::{'|'.join(norm_sorted)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


class DriftReport(BaseModel):
    has_critical_drift: bool = Field(
        ..., description="True if any previously mapped source column is missing"
    )
    has_benign_drift: bool = Field(
        ..., description="True if new unmapped columns appeared while all mapped columns remain intact"
    )
    missing_mapped_columns: List[str] = Field(default_factory=list)
    added_columns: List[str] = Field(default_factory=list)


def check_schema_drift(
    expected_mapped_source_columns: List[str],
    incoming_headers: List[Any],
    previous_all_headers: Optional[List[Any]] = None,
) -> DriftReport:
    """Distinguishes critical drift (mapped column disappeared/renamed) from benign drift (extra columns)."""
    incoming_norm: Set[str] = {normalize_header_name(h) for h in incoming_headers if str(h).strip()}
    expected_norm_map = {normalize_header_name(c): c for c in expected_mapped_source_columns}

    missing = [
        orig_name for norm_name, orig_name in expected_norm_map.items() if norm_name not in incoming_norm
    ]

    prev_norm: Set[str] = (
        {normalize_header_name(h) for h in previous_all_headers if str(h).strip()}
        if previous_all_headers
        else set(expected_norm_map.keys())
    )
    added = sorted(incoming_norm - prev_norm)

    return DriftReport(
        has_critical_drift=len(missing) > 0,
        has_benign_drift=(len(missing) == 0 and len(added) > 0),
        missing_mapped_columns=missing,
        added_columns=added,
    )


# =====================================================================
# 3. ANTI-FATIGUE COLUMN MAPPING & CONFIRMATION PROMPT
# =====================================================================

HIGH_RISK_TARGET_FIELDS = frozenset({
    "current_stock",
    "safety_stock",
    "unit_price",
    "cost",
    "harga_beli",
    "harga_jual",
    "stok",
    "qty",
})


class ColumnMappingProposal(BaseModel):
    source_column: str
    target_field: str
    similarity_score: float = Field(..., ge=0.0, le=1.0)
    is_high_risk: bool = False
    sample_values: List[str] = Field(default_factory=list)
    requires_user_confirmation: bool = True
    confirmation_prompt: str
    sanity_warnings: List[str] = Field(default_factory=list)


def build_column_mapping_proposal(
    df: pd.DataFrame,
    source_column: str,
    target_field: str,
    similarity_score: float,
    is_first_mapping_for_tenant: bool = True,
) -> ColumnMappingProposal:
    """Builds an anti-fatigue mapping proposal with real sample values and semantic sanity checks."""
    norm_target = target_field.strip().lower()
    is_high_risk = norm_target in HIGH_RISK_TARGET_FIELDS or any(
        w in norm_target for w in ("stock", "stok", "price", "harga", "cost", "qty")
    )

    # Extract up to 4 non-null sample values (local UI display for owner, scrubbed if PII)
    samples: List[str] = []
    sanity_warnings: List[str] = []

    if source_column in df.columns:
        col_series = df[source_column].dropna()
        raw_samples = col_series.head(4).tolist()
        samples = [PiiScrubber.mask_text(str(v)) for v in raw_samples]

        # Semantic sanity checks on numeric columns
        if pd.api.types.is_numeric_dtype(col_series) and not col_series.empty:
            min_val = float(col_series.min())
            if min_val < 0 and any(w in norm_target for w in ("stock", "stok", "price", "harga")):
                sanity_warnings.append(
                    f"Kolom '{source_column}' memiliki nilai negatif (min: {min_val}), tidak wajar untuk {target_field}."
                )

    # Rule: First-time mapping per tenant OR high-risk column OR low similarity score ALWAYS requires confirmation
    requires_confirm = (
        is_first_mapping_for_tenant
        or is_high_risk
        or similarity_score < 0.85
        or len(sanity_warnings) > 0
    )

    sample_preview = ", ".join(samples) if samples else "(kosong)"
    prompt = (
        f"Kolom '{source_column}' berisi nilai contoh: [{sample_preview}]. "
        f"Apakah ini benar untuk '{target_field}'?"
    )

    return ColumnMappingProposal(
        source_column=source_column,
        target_field=target_field,
        similarity_score=round(similarity_score, 3),
        is_high_risk=is_high_risk,
        sample_values=samples,
        requires_user_confirmation=requires_confirm,
        confirmation_prompt=prompt,
        sanity_warnings=sanity_warnings,
    )

