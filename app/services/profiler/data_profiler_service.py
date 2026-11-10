"""Data Profiling, Normalization, and Zero-Value Egress Module.

Designed specifically for messy Indonesian UMKM business spreadsheets:
- Strips Indonesian currency syntax (Rp 15.000, 15.000,00, 15k).
- Normalizes quantity strings with attached units (e.g. '910.00 pcs', '420.00 ml', '12,5 kg').
- Handles placeholder symbols ('-', 'N/A', 'kosong') as valid zeros or nulls.
- Computes statistical summaries without leaking raw row values to external LLMs (Zero-Value Egress).
"""

import re
from typing import Any, Dict, List, Optional, Tuple, Union
import pandas as pd


# Regex pattern to capture Indonesian currency: Rp, Rp., rP, with thousand separators
_RP_PATTERN = re.compile(r"^\s*rp\.?\s*([0-9\.,kK]+)\s*$", re.IGNORECASE)

# Regex pattern to capture numeric values with attached physical units (pcs, ml, kg, gram, gr, l, liter, bks, dus, pack, sak)
_QUANTITY_UNIT_PATTERN = re.compile(
    r"^\s*([+-]?\d+(?:[\.,]\d+)?)\s*([a-zA-Z]+|\b\w+\b)?\s*$", re.IGNORECASE
)

# Common blank/dash placeholders used in Indonesian ledgers
_BLANK_PLACEHOLDERS = {"-", "--", "---", "none", "null", "n/a", "na", "", "kosong", "nihil"}


def parse_numeric_cell(val: Any) -> Tuple[Optional[float], Optional[str]]:
    """Parses a messy Indonesian cell into (float_value, optional_unit).

    Examples:
        '910.00 pcs' -> (910.0, 'pcs')
        '420.00 ml'  -> (420.0, 'ml')
        '12,5 kg'    -> (12.5, 'kg')
        '-'          -> (0.0, None)
        'Rp 15.000'  -> (15000.0, 'IDR')
        '15k'        -> (15000.0, 'IDR')
        123.45       -> (123.45, None)
        None         -> (None, None)
    """
    if val is None or pd.isna(val):
        return None, None

    if isinstance(val, (int, float)):
        return float(val), None

    s = str(val).strip()
    if s.lower() in _BLANK_PLACEHOLDERS:
        return 0.0, None

    # 1. Check for Rupiah pattern
    rp_match = _RP_PATTERN.match(s)
    if rp_match:
        num_str = rp_match.group(1).strip()
        num_clean = _parse_indonesian_number_str(num_str)
        return num_clean, "IDR"

    # 2. Check for general quantity + unit (e.g. '910.00 pcs', '450 ml')
    qty_match = _QUANTITY_UNIT_PATTERN.match(s)
    if qty_match:
        num_str = qty_match.group(1)
        unit = qty_match.group(2)
        unit_str = unit.lower() if unit else None

        # Special case: check if unit is 'k' or 'rb' indicating thousands
        if unit_str in ("k", "rb"):
            base_num = _parse_indonesian_number_str(num_str)
            return (base_num * 1000.0 if base_num is not None else None), "IDR"

        parsed_num = _parse_indonesian_number_str(num_str)
        return parsed_num, unit_str

    # Fallback attempt
    try:
        clean_num = _parse_indonesian_number_str(s)
        return clean_num, None
    except Exception:
        return None, None


def _parse_indonesian_number_str(s: str) -> Optional[float]:
    """Converts strings like '15.000,50' or '15,000.50' or '15.000' or '15000' to float."""
    if not s:
        return None
    s = s.strip()

    # Handle '15k' or '15K'
    if s.lower().endswith("k"):
        s = s[:-1].strip()
        mult = 1000.0
    else:
        mult = 1.0

    # If both dot and comma exist:
    # Indonesian format: 15.000,50 -> dot is thousand, comma is decimal
    # Standard format:   15,000.50 -> comma is thousand, dot is decimal
    if "." in s and "," in s:
        if s.rfind(",") > s.rfind("."):
            # Indonesian: replace thousand dots with empty, comma with dot
            s = s.replace(".", "").replace(",", ".")
        else:
            # Standard: replace thousand comma with empty
            s = s.replace(",", "")
    elif "," in s:
        # Only comma: could be decimal (12,5) or Indonesian thousand (15,000)
        parts = s.split(",")
        if len(parts) == 2 and len(parts[1]) != 3:
            # Likely decimal like 12,5
            s = s.replace(",", ".")
        else:
            # Likely thousand separator like 15,000
            s = s.replace(",", "")
    elif "." in s:
        # Only dot:
        # Case A: 15.000 or 15.000.000 (Indonesian thousand format without comma)
        # Case B: 910.00 or 12.5 (decimal)
        parts = s.split(".")
        if len(parts) > 2:
            # Multiple dots e.g. 1.500.000 -> definitely thousand separators
            s = s.replace(".", "")
        elif len(parts) == 2:
            left, right = parts[0], parts[1]
            if len(right) == 3 and not right.startswith("00"):
                # Typical thousand e.g. 15.000, 15.250
                s = s.replace(".", "")
            elif len(right) == 3 and right == "000":
                # Explicit 15.000 -> 15000
                s = s.replace(".", "")
            else:
                # 910.00, 12.5, etc. keep as decimal dot
                pass

    try:
        return float(s) * mult
    except ValueError:
        return None


class DataProfilerService:
    """Service to profile and clean raw UMKM tabular data."""

    @staticmethod
    def clean_inventory_dataframe(df: pd.DataFrame) -> Tuple[pd.DataFrame, Dict[str, str]]:
        """Cleans messy quantity columns, strips units, and normalizes column headers.

        Returns:
            (cleaned_df, detected_units_map)
        """
        cleaned = df.copy()
        # 1. Normalize headers (lowercase, trim, strip multiple spaces)
        header_mapping = {
            col: " ".join(str(col).strip().split())
            for col in cleaned.columns
        }
        cleaned.rename(columns=header_mapping, inplace=True)

        detected_units: Dict[str, str] = {}

        # 2. Iterate columns and inspect string/object columns
        for col in cleaned.columns:
            if cleaned[col].dtype == object or pd.api.types.is_string_dtype(cleaned[col]):
                # Sample non-null values
                sample = cleaned[col].dropna().astype(str).tolist()
                if not sample:
                    continue

                parsed_cells = [parse_numeric_cell(v) for v in sample]
                valid_numbers = [p[0] for p in parsed_cells if p[0] is not None]

                # If at least 80% of non-null cells are parseable numbers, normalize column
                if len(valid_numbers) >= 0.8 * len(sample):
                    # Extract dominant unit if any
                    units = [p[1] for p in parsed_cells if p[1] is not None]
                    dominant_unit = max(set(units), key=units.count) if units else None
                    if dominant_unit:
                        detected_units[col] = dominant_unit

                    # Convert entire column
                    def _conv(val: Any) -> Optional[float]:
                        num, _ = parse_numeric_cell(val)
                        return num

                    cleaned[col] = cleaned[col].apply(_conv)

        return cleaned, detected_units

    @staticmethod
    def get_zero_value_egress_summary(df: pd.DataFrame) -> Dict[str, Any]:
        """Generates statistical metadata for synthesis without leaking raw row values (UU PDP)."""
        summary = {
            "total_rows": len(df),
            "columns": []
        }
        for col in df.columns:
            col_data = df[col]
            null_count = int(col_data.isna().sum())
            null_ratio = float(null_count / len(df)) if len(df) > 0 else 0.0

            col_info: Dict[str, Any] = {
                "name": str(col),
                "inferred_type": str(col_data.dtype),
                "null_ratio": round(null_ratio, 3),
                "unique_values_count": int(col_data.nunique(dropna=True)),
            }

            if pd.api.types.is_numeric_dtype(col_data):
                non_null = col_data.dropna()
                if not non_null.empty:
                    col_info["min"] = float(non_null.min())
                    col_info["max"] = float(non_null.max())
                    col_info["mean"] = round(float(non_null.mean()), 2)
            summary["columns"].append(col_info)

        return summary
