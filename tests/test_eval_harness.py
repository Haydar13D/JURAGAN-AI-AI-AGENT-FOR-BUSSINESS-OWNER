"""
tests/test_eval_harness.py
T16 — Memvalidasi schema evals/golden_set.jsonl tanpa network:
- Minimal 40 kasus
- Field wajib: id, category, pertanyaan, expected_tools, forbidden_tools, notes
- Semua 6 kategori wajib hadir: analytics, inventory, pricing, workflow, ambiguity, safety
- Seluruh id unik
"""

import json
from pathlib import Path

GOLDEN_SET_PATH = Path(__file__).resolve().parent.parent / "evals" / "golden_set.jsonl"
REQUIRED_CATEGORIES = {"analytics", "inventory", "pricing", "workflow", "ambiguity", "safety"}
REQUIRED_FIELDS = {"id", "category", "pertanyaan", "expected_tools", "forbidden_tools", "notes"}


def _load_cases():
    assert GOLDEN_SET_PATH.exists(), f"File tidak ditemukan: {GOLDEN_SET_PATH}"
    cases = []
    for line_no, raw in enumerate(GOLDEN_SET_PATH.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = raw.strip()
        if not stripped:
            continue
        cases.append((line_no, json.loads(stripped)))
    return cases


def test_golden_set_minimum_count_and_unique_ids():
    rows = _load_cases()
    assert len(rows) >= 40, f"Golden set minimal 40 kasus, saat ini {len(rows)}"
    ids = [case["id"] for _, case in rows]
    assert len(ids) == len(set(ids)), "Terdapat id duplikat di golden_set.jsonl"


def test_golden_set_schema_and_all_required_categories():
    rows = _load_cases()
    seen_categories = set()

    for line_no, case in rows:
        missing = REQUIRED_FIELDS - set(case.keys())
        assert not missing, f"Baris {line_no} kekurangan field wajib: {missing}"
        assert isinstance(case["id"], str) and case["id"].strip()
        assert case["category"] in REQUIRED_CATEGORIES, f"Baris {line_no} kategori tidak valid: {case['category']}"
        assert isinstance(case["pertanyaan"], str) and case["pertanyaan"].strip()
        assert isinstance(case["expected_tools"], list)
        assert isinstance(case["forbidden_tools"], list)
        assert isinstance(case["notes"], str) and case["notes"].strip()
        if "expected_facts" in case and case["expected_facts"] is not None:
            assert isinstance(case["expected_facts"], dict)
        seen_categories.add(case["category"])

    assert seen_categories == REQUIRED_CATEGORIES, f"Kategori belum lengkap: {REQUIRED_CATEGORIES - seen_categories}"
