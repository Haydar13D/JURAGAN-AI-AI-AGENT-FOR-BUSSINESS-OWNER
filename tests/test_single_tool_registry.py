"""
tests/test_single_tool_registry.py
T3 — Satu registry, dan schema di-generate.
Memverifikasi:
1. BaseTool.to_openai_tool() menghasilkan skema function-calling OpenAI dari Pydantic.
2. Payload tools yang dikirim ke LLM client oleh DeepSeekAgentService sama persis dengan tool_type di ToolRegistry.
3. ToolRegistry.get() memilih versi terbaru menggunakan semver (1.10.0 > 1.9.0), bukan sort string.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel, Field

from app.core.tool_registry import (
    BaseTool,
    ExecutionContext,
    ToolCategory,
    ToolMetadata,
    ToolRegistry,
    ToolRiskTier,
)
from app.services.deepseek_agent import DeepSeekAgentService, register_analytics_tools


class _DummyInput(BaseModel):
    query: str = Field(..., description="Query pencarian")


class _DummyOutput(BaseModel):
    ok: bool = True


class _DummyToolV19(BaseTool):
    metadata = ToolMetadata(
        tool_type="test.semver_dummy",
        version="1.9.0",
        display_name="Dummy 1.9",
        description="Versi 1.9.0",
        category=ToolCategory.ANALYSIS,
        risk_tier=ToolRiskTier.NONE,
    )
    input_schema = _DummyInput
    output_schema = _DummyOutput

    async def execute(self, config, inputs, context):
        return _DummyOutput(ok=True)


class _DummyToolV110(BaseTool):
    metadata = ToolMetadata(
        tool_type="test.semver_dummy",
        version="1.10.0",
        display_name="Dummy 1.10",
        description="Versi 1.10.0",
        category=ToolCategory.ANALYSIS,
        risk_tier=ToolRiskTier.NONE,
    )
    input_schema = _DummyInput
    output_schema = _DummyOutput

    async def execute(self, config, inputs, context):
        return _DummyOutput(ok=True)


def test_to_openai_tool_schema_generation():
    tool = _DummyToolV110()
    schema = tool.to_openai_tool()

    assert schema["type"] == "function"
    assert schema["function"]["name"] == "test.semver_dummy"
    assert schema["function"]["description"] == "Versi 1.10.0"
    assert schema["function"]["parameters"] == _DummyInput.model_json_schema()


def test_tool_registry_semver_ordering():
    ToolRegistry.register(_DummyToolV19())
    ToolRegistry.register(_DummyToolV110())

    latest = ToolRegistry.get("test.semver_dummy")
    assert latest is not None
    assert latest.metadata.version == "1.10.0"


@pytest.mark.asyncio
async def test_deepseek_agent_sends_registry_tools_payload(monkeypatch):
    register_analytics_tools()
    captured_payloads = []

    class FakeCompletions:
        async def create(self, **kwargs):
            captured_payloads.append(kwargs)
            msg = SimpleNamespace(content="Ringkasan siap.", tool_calls=None)
            return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=FakeCompletions())
    )

    monkeypatch.setattr(
        DeepSeekAgentService,
        "_get_active_providers",
        classmethod(lambda cls: [{"name": "FakeProvider", "client": fake_client, "model": "fake-model"}]),
    )
    monkeypatch.setattr(
        DeepSeekAgentService,
        "get_or_create_session",
        AsyncMock(return_value=(SimpleNamespace(id=1, session_uuid="sess_test_t3"), [])),
    )
    monkeypatch.setattr(
        DeepSeekAgentService,
        "save_turn",
        AsyncMock(return_value=None),
    )

    fake_session = MagicMock()
    resp = await DeepSeekAgentService.chat(
        session=fake_session,
        user_message="Berapa omzet hari ini?",
        outlet_id=1,
    )

    assert resp.reply == "Ringkasan siap."
    assert len(captured_payloads) == 1

    sent_tools = captured_payloads[0]["tools"]
    sent_names = {t["function"]["name"] for t in sent_tools}
    registry_names = {t.metadata.tool_type for t in ToolRegistry.get_all_latest()}

    assert sent_names == registry_names
    assert "analytics.get_sales_overview" in sent_names
    assert "analytics.get_top_products" in sent_names
