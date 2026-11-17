"""
tests/test_agent_loop.py
T4 — Agent loop dengan guard (tanpa network).
Memverifikasi:
1. LLM panggil 1 tool di langkah 1 -> jawaban final di langkah 2 -> status DONE, tool dipanggil 1x.
2. LLM panggil tool terus sampai 12 langkah -> status MAX_STEPS, total panggilan == 12.
3. Tool melempar exception -> loop tidak crash, error masuk ke role='tool', loop lanjut hingga DONE.
4. Guard output truncation (8000 char) dan MAX_TOOL_CALLS_PER_STEP (8) bekerja.
"""

import json
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, Field

from app.core.tool_registry import BaseTool, ToolCategory, ToolMetadata, ToolRiskTier
from app.services.agent_loop import (
    MAX_TOOL_CALLS_PER_STEP,
    MAX_TOOL_OUTPUT_CHARS,
    AgentStatus,
    run_agent,
)


class _PingInput(BaseModel):
    msg: str = Field(default="hi")


class _PingOutput(BaseModel):
    reply: str


class _SpyTool(BaseTool):
    metadata = ToolMetadata(
        tool_type="test.spy_ping",
        version="1.0.0",
        display_name="Spy Ping",
        description="Tool uji untuk agent loop.",
        category=ToolCategory.ANALYSIS,
        risk_tier=ToolRiskTier.NONE,
    )
    input_schema = _PingInput
    output_schema = _PingOutput

    def __init__(self, fail: bool = False, big_payload: bool = False):
        self.calls = 0
        self.fail = fail
        self.big_payload = big_payload

    async def execute(self, config, inputs: _PingInput, context) -> _PingOutput:
        self.calls += 1
        if self.fail:
            raise RuntimeError("Database stok sedang tidak merespons")
        if self.big_payload:
            return _PingOutput(reply="X" * (MAX_TOOL_OUTPUT_CHARS + 500))
        return _PingOutput(reply=f"pong:{inputs.msg}")


def _make_tool_call(call_id: str, name: str, args: dict):
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=json.dumps(args)),
    )


def _make_fake_client(responses):
    idx = {"i": 0}

    class _Completions:
        async def create(self, **kwargs):
            resp_msg = responses[idx["i"]]
            idx["i"] += 1
            return SimpleNamespace(choices=[SimpleNamespace(message=resp_msg)])

    return SimpleNamespace(chat=SimpleNamespace(completions=_Completions()))


@pytest.mark.asyncio
async def test_agent_loop_single_tool_then_done():
    spy = _SpyTool()
    client = _make_fake_client([
        SimpleNamespace(
            content="",
            tool_calls=[_make_tool_call("c1", "test.spy_ping", {"msg": "kopi"})],
        ),
        SimpleNamespace(
            content="Hasil pengecekan kopi sudah selesai.",
            tool_calls=None,
        ),
    ])

    res = await run_agent(
        goal="Cek kopi",
        allowed_tools=[spy],
        max_steps=12,
        llm_client=client,
    )

    assert res.status == AgentStatus.DONE
    assert res.steps_taken == 2
    assert res.tool_calls_count == 1
    assert spy.calls == 1
    assert "kopi" in res.reply


@pytest.mark.asyncio
async def test_agent_loop_hits_max_steps():
    spy = _SpyTool()
    infinite_calls = [
        SimpleNamespace(
            content="",
            tool_calls=[_make_tool_call(f"c{i}", "test.spy_ping", {"msg": str(i)})],
        )
        for i in range(12)
    ]
    client = _make_fake_client(infinite_calls)

    res = await run_agent(
        goal="Loop terus",
        allowed_tools=[spy],
        max_steps=12,
        llm_client=client,
    )

    assert res.status == AgentStatus.MAX_STEPS
    assert res.steps_taken == 12
    assert res.tool_calls_count == 12
    assert spy.calls == 12


@pytest.mark.asyncio
async def test_agent_loop_tool_exception_self_recovers():
    failing_spy = _SpyTool(fail=True)
    client = _make_fake_client([
        SimpleNamespace(
            content="",
            tool_calls=[_make_tool_call("c_err", "test.spy_ping", {"msg": "cek"})],
        ),
        SimpleNamespace(
            content="Maaf, tool mengalami kendala: Database stok sedang tidak merespons.",
            tool_calls=None,
        ),
    ])

    res = await run_agent(
        goal="Cek stok",
        allowed_tools=[failing_spy],
        max_steps=12,
        llm_client=client,
    )

    assert res.status == AgentStatus.DONE
    assert failing_spy.calls == 1
    assert res.traces[0].error == "Database stok sedang tidak merespons"
    tool_msgs = [m for m in res.messages if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    assert "error" in json.loads(tool_msgs[0]["content"])


@pytest.mark.asyncio
async def test_agent_loop_guards_truncation_and_max_calls_per_step():
    big_spy = _SpyTool(big_payload=True)
    too_many_calls = [
        _make_tool_call(f"c{i}", "test.spy_ping", {"msg": str(i)})
        for i in range(MAX_TOOL_CALLS_PER_STEP + 3)
    ]
    client = _make_fake_client([
        SimpleNamespace(content="", tool_calls=too_many_calls),
        SimpleNamespace(content="Selesai dengan batas guard.", tool_calls=None),
    ])

    res = await run_agent(
        goal="Uji guard",
        allowed_tools=[big_spy],
        max_steps=12,
        llm_client=client,
    )

    assert res.status == AgentStatus.DONE
    assert big_spy.calls == MAX_TOOL_CALLS_PER_STEP
    assert res.tool_calls_count == MAX_TOOL_CALLS_PER_STEP
    tool_msgs = [m for m in res.messages if m.get("role") == "tool"]
    assert len(tool_msgs) == MAX_TOOL_CALLS_PER_STEP + 3
    assert len(tool_msgs[0]["content"]) <= MAX_TOOL_OUTPUT_CHARS
    assert "Melebihi batas maksimal" in tool_msgs[-1]["content"]
