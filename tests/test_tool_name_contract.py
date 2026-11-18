"""
tests/test_tool_name_contract.py
Contract tests untuk invarian I15 (T13):
- Semua tool di registry menghasilkan wire name yang lolos ^[a-zA-Z0-9_-]{1,64}$.
- Round-trip from_wire_name(to_wire_name(t)) == t untuk setiap tool terdaftar.
- Tabrakan nama (mis. a.b__c vs a__b.c) ditolak saat ToolRegistry.register().
- Payload asli yang dikirim agent_loop ke LLM client valid dan lookup tool call memakai decode.
"""

from types import SimpleNamespace
from typing import Any, Dict, List

import pytest
from pydantic import BaseModel

from app.core.tool_registry import (
    BaseTool,
    ExecutionContext,
    ToolCategory,
    ToolMetadata,
    ToolRegistry,
    ToolRiskTier,
    WIRE_NAME_RE,
    from_wire_name,
    to_wire_name,
)
from app.services.agent_loop import AgentStatus, run_agent

# Pastikan semua modul tool produksi ter-import dan teregister di ToolRegistry.
import app.core.governance_tools  # noqa: F401
import app.services.deepseek_agent  # noqa: F401
import app.services.fnb_marketing.tools  # noqa: F401
import app.services.inventory_pack.tools  # noqa: F401
import app.services.retail_pricing.tools  # noqa: F401
from app.services.workflow_tool import register_workflow_tool

register_workflow_tool()

ALL_REGISTERED_TOOLS = ToolRegistry.get_all_latest()
ALL_TOOL_TYPES = [t.metadata.tool_type for t in ALL_REGISTERED_TOOLS]


def test_registry_is_not_empty():
    assert len(ALL_TOOL_TYPES) > 0, "ToolRegistry tidak boleh kosong saat contract test."


@pytest.mark.parametrize("tool_type", ALL_TOOL_TYPES)
def test_registered_tool_wire_name_contract_and_roundtrip(tool_type: str):
    wire = to_wire_name(tool_type)
    assert len(wire) <= 64
    assert WIRE_NAME_RE.match(wire), f"Wire name '{wire}' melanggar kontrak provider"
    assert from_wire_name(wire) == tool_type

    tool = ToolRegistry.get(tool_type)
    assert tool is not None
    schema = tool.to_openai_tool()
    assert schema["function"]["name"] == wire


class _DummyInput(BaseModel):
    text: str = "ok"


class _DummyOutput(BaseModel):
    echo: str


def _make_dummy_tool(tool_type: str) -> BaseTool:
    class _DummyTool(BaseTool):
        metadata = ToolMetadata(
            tool_type=tool_type,
            version="1.0.0",
            display_name=tool_type,
            description="Dummy tool for wire name contract tests",
            category=ToolCategory.ANALYSIS,
            risk_tier=ToolRiskTier.NONE,
        )
        input_schema = _DummyInput
        output_schema = _DummyOutput

        async def execute(self, config: BaseModel, inputs: _DummyInput, context: ExecutionContext) -> BaseModel:
            return _DummyOutput(echo=inputs.text)

    return _DummyTool()


def test_wire_name_collision_rejected_on_register():
    saved_registry = {k: dict(v) for k, v in ToolRegistry._registry.items()}
    try:
        ToolRegistry.register(_make_dummy_tool("a.b__c"))
        with pytest.raises(ValueError, match="Tabrakan wire name"):
            ToolRegistry.register(_make_dummy_tool("a__b.c"))
    finally:
        ToolRegistry._registry.clear()
        ToolRegistry._registry.update(saved_registry)


def test_to_wire_name_rejects_invalid_chars_and_length():
    with pytest.raises(ValueError):
        to_wire_name("invalid tool name!")
    with pytest.raises(ValueError):
        to_wire_name("x." + "a" * 63)


@pytest.mark.asyncio
async def test_agent_loop_sends_valid_wire_names_and_decodes_tool_call():
    dotted_tool = _make_dummy_tool("demo.ping_pong")
    captured_tools_payloads: List[List[Dict[str, Any]]] = []
    turn = 0

    class _FakeCompletions:
        async def create(self, **kwargs: Any) -> Any:
            nonlocal turn
            turn += 1
            captured_tools_payloads.append(kwargs.get("tools") or [])
            if turn == 1:
                call = SimpleNamespace(
                    id="call_1",
                    type="function",
                    function=SimpleNamespace(
                        name="demo__ping_pong",
                        arguments='{"text": "halo"}',
                    ),
                )
                msg = SimpleNamespace(content="", tool_calls=[call])
            else:
                msg = SimpleNamespace(content="Selesai: halo", tool_calls=None)
            return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=_FakeCompletions()))

    result = await run_agent(
        goal="tolong ping",
        allowed_tools=[dotted_tool],
        llm_client=fake_client,
        model="fake-model",
    )

    assert result.status == AgentStatus.DONE
    assert result.reply == "Selesai: halo"
    assert len(result.traces) == 1
    assert result.traces[0].error is None
    assert result.traces[0].output == {"echo": "halo"}

    assert len(captured_tools_payloads) >= 1
    for tool_def in captured_tools_payloads[0]:
        fn_name = tool_def["function"]["name"]
        assert WIRE_NAME_RE.match(fn_name), f"Payload tool name '{fn_name}' tidak valid untuk provider"
