"""Tests for DataProfilerService against real-world Indonesian UMKM fixtures."""

import os
import pandas as pd
import pytest

from app.services.profiler.data_profiler_service import (
    DataProfilerService,
    parse_numeric_cell,
)
from app.services.profiler.schema_mapper import (
    PiiScrubber,
    build_column_mapping_proposal,
    check_schema_drift,
    compute_header_signature,
)


FIXTURE_CSV_PATH = os.path.join(
    os.path.dirname(__file__), "fixtures", "real_umkm", "dev", "fixture_02_kartu_stok_bahan.csv"
)


def test_parse_numeric_cell_variations():
    """Verify various messy Indonesian quantity and currency strings."""
    assert parse_numeric_cell("910.00 pcs") == (910.0, "pcs")
    assert parse_numeric_cell("420.00 ml") == (420.0, "ml")
    assert parse_numeric_cell("12,5 kg") == (12.5, "kg")
    assert parse_numeric_cell("-") == (0.0, None)
    assert parse_numeric_cell("Rp 15.000") == (15000.0, "IDR")
    assert parse_numeric_cell("Rp 15.000,50") == (15000.5, "IDR")
    assert parse_numeric_cell("15k") == (15000.0, "IDR")
    assert parse_numeric_cell(None) == (None, None)


def test_clean_real_umkm_fixture_02():
    """Verify DataProfilerService cleans real fixture_02_kartu_stok_bahan.csv without errors."""
    assert os.path.exists(FIXTURE_CSV_PATH), f"Fixture not found at {FIXTURE_CSV_PATH}"

    raw_df = pd.read_csv(FIXTURE_CSV_PATH)
    assert len(raw_df) > 0

    # Before cleaning: 'Stok Awal' and 'Stok Akhir' are non-numeric types because of 'pcs' and 'ml'
    assert not pd.api.types.is_numeric_dtype(raw_df["Stok Awal"])
    assert not pd.api.types.is_numeric_dtype(raw_df["Stok Akhir"])

    # Run profiler cleaner
    cleaned_df, detected_units = DataProfilerService.clean_inventory_dataframe(raw_df)

    # 1. Quantity columns must be numeric floats
    for col in ["Stok Awal", "Tambah Stok", "Kurangi Stok", "Stok Akhir"]:
        assert pd.api.types.is_numeric_dtype(cleaned_df[col]), f"Column {col} is not numeric!"

    # 2. Verify specific cell values
    # First row: Beef, Stok Awal: 910.00 pcs -> 910.0
    assert cleaned_df.loc[0, "Stok Awal"] == 910.0
    assert cleaned_df.loc[0, "Tambah Stok"] == 5.0
    assert cleaned_df.loc[0, "Kurangi Stok"] == 0.0  # was '-'
    assert cleaned_df.loc[0, "Stok Akhir"] == 915.0

    # 3. Check detected dominant units
    assert "Stok Awal" in detected_units
    assert detected_units["Stok Awal"] == "pcs"


def test_zero_value_egress_summary():
    """Verify zero-value egress summary creates statistical metadata without raw values."""
    raw_df = pd.read_csv(FIXTURE_CSV_PATH)
    cleaned_df, _ = DataProfilerService.clean_inventory_dataframe(raw_df)

    summary = DataProfilerService.get_zero_value_egress_summary(cleaned_df)

    assert "total_rows" in summary
    assert summary["total_rows"] == len(cleaned_df)
    assert "columns" in summary

    col_names = [c["name"] for c in summary["columns"]]
    assert "Stok Akhir" in col_names

    stok_akhir_meta = next(c for c in summary["columns"] if c["name"] == "Stok Akhir")
    assert "min" in stok_akhir_meta
    assert "max" in stok_akhir_meta
    assert "mean" in stok_akhir_meta
    # Ensure raw customer text or raw order IDs are not dumped into col_info
    assert "raw_values" not in stok_akhir_meta


def test_header_signature_ignores_whitespace_and_case():
    """Verify normalized header signature is deterministic across formatting variations."""
    h1 = ["Kode", "Nama Bahan Baku", "Stok Akhir"]
    h2 = ["  kode ", "nama   bahan baku", "STOK AKHIR"]
    assert compute_header_signature("tenant_1", h1) == compute_header_signature("tenant_1", h2)
    assert compute_header_signature("tenant_1", h1) != compute_header_signature("tenant_2", h1)


def test_critical_vs_benign_schema_drift():
    """Verify extra unmapped columns are benign while missing mapped columns are critical."""
    mapped_cols = ["Kode", "Nama Bahan Baku", "Stok Akhir"]

    # Week 2 adds an extra column 'Catatan Gudang' -> Benign drift, workflow continues
    drift_benign = check_schema_drift(
        expected_mapped_source_columns=mapped_cols,
        incoming_headers=["Kode", "Nama Bahan Baku", "Stok Akhir", "Catatan Gudang"],
        previous_all_headers=mapped_cols,
    )
    assert drift_benign.has_critical_drift is False
    assert drift_benign.has_benign_drift is True
    assert "catatan gudang" in drift_benign.added_columns

    # Week 3 renames 'Stok Akhir' to 'Saldo' -> Critical drift, workflow must pause
    drift_crit = check_schema_drift(
        expected_mapped_source_columns=mapped_cols,
        incoming_headers=["Kode", "Nama Bahan Baku", "Saldo"],
        previous_all_headers=mapped_cols,
    )
    assert drift_crit.has_critical_drift is True
    assert "Stok Akhir" in drift_crit.missing_mapped_columns


def test_anti_fatigue_mapping_proposal_and_pii_scrubber():
    """Verify high-risk columns always require confirmation and display sample values."""
    raw_df = pd.read_csv(FIXTURE_CSV_PATH)
    cleaned_df, _ = DataProfilerService.clean_inventory_dataframe(raw_df)

    # High-risk target 'current_stock' must require confirmation even if similarity is 0.99 and not first run
    prop = build_column_mapping_proposal(
        df=cleaned_df,
        source_column="Stok Akhir",
        target_field="current_stock",
        similarity_score=0.99,
        is_first_mapping_for_tenant=False,
    )
    assert prop.is_high_risk is True
    assert prop.requires_user_confirmation is True
    assert "915.0" in prop.confirmation_prompt

    # Verify PII Scrubber masks Indonesian phone and NIK
    dirty_note = "Hubungi 081234567890 atau NIK 3201234567890001 email budi@toko.id"
    masked = PiiScrubber.mask_text(dirty_note)
    assert "081234567890" not in masked
    assert "3201234567890001" not in masked
    assert "budi@toko.id" not in masked
    assert "[PHONE_MASKED]" in masked
    assert "[NIK_MASKED]" in masked
    assert "[EMAIL_MASKED]" in masked

