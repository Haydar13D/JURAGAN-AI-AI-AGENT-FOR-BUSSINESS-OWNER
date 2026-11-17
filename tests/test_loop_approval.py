"""
tests/test_loop_approval.py
T6 — Approval pindah ke loop.
Memverifikasi:
1. Tool dengan risk_tier EXTERNAL/FINANCIAL berhenti sebelum eksekusi (spy == 0) dan mengembalikan APPROVAL_REQUIRED.
2. resume_with_approval(result, approved=True) mengeksekusi tool tertunda (spy == 1) dan melanjutkan loop hingga DONE tanpa mengulang step sebelumnya.
3. resume_with_approval(result, approved=False) mengembalikan status REJECTED dan tool tidak pernah dipanggil (spy == 0).
"""

import json
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, Field

from app.core.tool_registry import BaseTool, ToolCategory, ToolMetadata, ToolRiskTier
from app.services.agent_loop import AgentStatus, resume_with_approval, run_agent


class _AnalyzeInput(BaseModel):
    topic: str = Field(default="promo")


class _AnalyzeOutput(BaseModel):
    summary: str


class _PublishInput(BaseModel):
    caption: str = Field(default="Promo Spesial")


class _PublishOutput(BaseModel):
    post_id: str


class _SafeAnalyzeTool(BaseTool):
    metadata = ToolMetadata(
        tool_type="test.analyze",
        version="1.0.0",
        display_name="Analyze",
        description="Tool aman tanpa approval.",
        category=ToolCategory.ANALYSIS,
        risk_tier=ToolRiskTier.NONE,
    )
    input_schema = _AnalyzeInput
    output_schema = _AnalyzeOutput

    def __init__(self):
        self.calls = 0

    async def execute(self, config, inputs: _AnalyzeInput, context) -> _AnalyzeOutput:
        self.calls += 1
        return _AnalyzeOutput(summary=f"analisa:{inputs.topic}")


class _ExternalPublishTool(BaseTool):
    metadata = ToolMetadata(
        tool_type="test.publish",
        version="1.0.0",
        display_name="Publish External",
        description="Tool eksternal yang wajib approval.",
        category=ToolCategory.EXPORT,
        risk_tier=ToolRiskTier.EXTERNAL,
    )
    input_schema = _PublishInput
    output_schema = _PublishOutput

    def __init__(self):
        self.calls = 0

    async def execute(self, config, inputs: _PublishInput, context) -> _PublishOutput:
        self.calls += 1
        return _PublishOutput(post_id="ig_post_001")


def _make_call(call_id: str, name: str, args: dict):
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=json.dumps(args)),
    )


def _make_fake_client(responses):
    idx = {"i": 0}

    class _Completions:
        async def create(self, **kwargs):
            msg = responses[idx["i"]]
            idx["i"] += 1
            return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

    return SimpleNamespace(chat=SimpleNamespace(completions=_Completions()))


@pytest.mark.asyncio
async def test_loop_pauses_before_external_tool_and_resumes_on_approve():
    analyze_spy = _SafeAnalyzeTool()
    publish_spy = _ExternalPublishTool()
    client = _make_fake_client([
        SimpleNamespace(content="", tool_calls=[_make_call("c1", "test.analyze", {"topic": "kopi"})]),
        SimpleNamespace(content="", tool_calls=[_make_call("c2", "test.publish", {"caption": "Diskon Kopi"})]),
        SimpleNamespace(content="Kampanye berhasil dipublikasikan.", tool_calls=None),
    ])

    # (a) Tanpa approval -> stop sebelum publish dipanggil
    paused = await run_agent(
        goal="Analisa lalu publish promo kopi",
        allowed_tools=[analyze_spy, publish_spy],
        llm_client=client,
    )

    assert paused.status == AgentStatus.APPROVAL_REQUIRED
    assert analyze_spy.calls == 1
    assert publish_spy.calls == 0
    assert paused.pending_approval is not None
    assert paused.pending_approval["tool_name"] == "test.publish"
    assert paused.pending_approval["arguments"] == {"caption": "Diskon Kopi"}

    # (b) Approve -> publish dieksekusi 1x, analyze TIDAK diulang (tetap 1)
    resumed = await resume_with_approval(
        paused,
        allowed_tools=[analyze_spy, publish_spy],
        approved=True,
        llm_client=client,
    )

    assert resumed.status == AgentStatus.DONE
    assert analyze_spy.calls == 1
    assert publish_spy.calls == 1
    assert "dipublikasikan" in resumed.reply


@pytest.mark.asyncio
async def test_loop_reject_never_calls_external_tool():
    publish_spy = _ExternalPublishTool()
    client = _make_fake_client([
        SimpleNamespace(content="", tool_calls=[_make_call("c1", "test.publish", {"caption": "Promo"})]),
    ])

    paused = await run_agent(
        goal="Publish promo",
        allowed_tools=[publish_spy],
        llm_client=client,
    )
    assert paused.status == AgentStatus.APPROVAL_REQUIRED
    assert publish_spy.calls == 0

    # (c) Reject -> REJECTED dan spy tetap 0
    rejected = await resume_with_approval(
        paused,
        allowed_tools=[publish_spy],
        approved=False,
        llm_client=client,
    )
    assert rejected.status == AgentStatus.REJECTED
    assert publish_spy.calls == 0
