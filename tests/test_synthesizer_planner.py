"""
tests/test_synthesizer_planner.py
T7 — Ganti if/elif synthesizer dengan planner LLM.
Memverifikasi:
1. Fake LLM mengembalikan DAG valid -> is_supported=True, workflow lolos WorkflowValidator.
2. Fake LLM mengembalikan JSON rusak 3x -> is_supported=False, rejection_reason='LLM_UNAVAILABLE' (tidak jatuh ke DAG hardcoded).
3. Fake LLM mengembalikan tool_type fiktif -> ditolak WorkflowValidator (is_supported=False), bukan dipaksa lolos.
4. Provider LLM kosong -> is_supported=False, rejection_reason='LLM_UNAVAILABLE'.
"""

import json
from types import SimpleNamespace

import pytest

from app.services.inventory_pack import register_inventory_tools
from app.services.workflow_synthesizer import WorkflowSynthesizerService


@pytest.fixture(autouse=True)
def _register_tools():
    register_inventory_tools()


def _make_fake_provider(outputs, captured_prompts=None):
    idx = {"i": 0}

    class _Completions:
        async def create(self, **kwargs):
            if captured_prompts is not None:
                captured_prompts.append(kwargs["messages"])
            raw = outputs[min(idx["i"], len(outputs) - 1)]
            idx["i"] += 1
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=raw))]
            )

    return {
        "name": "FakePlannerLLM",
        "model": "fake-planner",
        "client": SimpleNamespace(chat=SimpleNamespace(completions=_Completions())),
    }


@pytest.mark.asyncio
async def test_synthesizer_planner_valid_dag_passes_validator(monkeypatch):
    valid_json = json.dumps({
        "is_supported": True,
        "name": "Alur Cek Stok LLM",
        "rationale": "Dirakit oleh planner LLM dari katalog resmi.",
        "nodes": [
            {"id": "ingest_stock", "tool_type": "inventory.ingest_stock_ledger", "config": {}, "input_bindings": {}},
            {"id": "filter_low", "tool_type": "inventory.filter_low_stock", "config": {"default_safety_stock": 50}, "input_bindings": {"snapshot": "ingest_stock.output"}},
            {"id": "draft_po", "tool_type": "inventory.draft_supplier_po", "config": {"store_name": "Toko"}, "input_bindings": {"report": "filter_low.output"}},
        ],
        "edges": [
            {"source": "ingest_stock", "target": "filter_low"},
            {"source": "filter_low", "target": "draft_po"},
        ],
    })
    captured = []
    provider = _make_fake_provider([valid_json], captured_prompts=captured)
    monkeypatch.setattr(WorkflowSynthesizerService, "_get_llm_provider", classmethod(lambda cls: provider))

    card = await WorkflowSynthesizerService.synthesize_from_problem(
        tenant_id="tenant_t7",
        user_problem="Cek stok bahan baku yang menipis",
    )

    assert card.is_supported is True
    assert card.workflow is not None
    assert card.validation is not None and card.validation.is_valid is True
    assert len(captured) == 1
    sys_prompt = captured[0][0]["content"]
    assert "inventory.ingest_stock_ledger" in sys_prompt
    assert "DILARANG mengarang tool_type" in sys_prompt


@pytest.mark.asyncio
async def test_synthesizer_planner_broken_json_3x_returns_llm_unavailable(monkeypatch):
    provider = _make_fake_provider(["{not-valid-json", "still broken", "<<<invalid>>>"])
    monkeypatch.setattr(WorkflowSynthesizerService, "_get_llm_provider", classmethod(lambda cls: provider))

    card = await WorkflowSynthesizerService.synthesize_from_problem(
        tenant_id="tenant_t7",
        user_problem="Tolong cek stok bahan baku burger saya",
    )

    assert card.is_supported is False
    assert card.workflow is None
    assert card.rejection_reason == "LLM_UNAVAILABLE"
    assert card.iterations_used == 3


@pytest.mark.asyncio
async def test_synthesizer_planner_fictional_tool_type_rejected_by_validator(monkeypatch):
    fictional_json = json.dumps({
        "is_supported": True,
        "name": "Workflow Fiktif",
        "rationale": "Pakai tool khayalan.",
        "nodes": [
            {"id": "n1", "tool_type": "magic.teleport_stock", "config": {}, "input_bindings": {}},
        ],
        "edges": [],
    })
    provider = _make_fake_provider([fictional_json, fictional_json, fictional_json])
    monkeypatch.setattr(WorkflowSynthesizerService, "_get_llm_provider", classmethod(lambda cls: provider))

    card = await WorkflowSynthesizerService.synthesize_from_problem(
        tenant_id="tenant_t7",
        user_problem="Kirim stok pakai teleporter",
    )

    assert card.is_supported is False
    assert card.workflow is None
    assert card.validation is not None and card.validation.is_valid is False
    assert any("magic.teleport_stock" in err for err in card.validation.errors)


@pytest.mark.asyncio
async def test_synthesizer_planner_no_provider_returns_llm_unavailable(monkeypatch):
    monkeypatch.setattr(WorkflowSynthesizerService, "_get_llm_provider", classmethod(lambda cls: None))

    card = await WorkflowSynthesizerService.synthesize_from_problem(
        tenant_id="tenant_t7",
        user_problem="Cek stok bahan baku",
    )

    assert card.is_supported is False
    assert card.workflow is None
    assert card.rejection_reason == "LLM_UNAVAILABLE"
