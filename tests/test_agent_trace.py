"""
tests/test_agent_trace.py
T17 / I17 — Memverifikasi persistensi AgentRun + AgentStep dan endpoint GET /api/v1/agent/runs/{run_id}:
- Fake LLM 2 langkah -> 1 AgentRun + 2 AgentStep
- Session DB baru (simulasi restart) & GET /runs/{run_id} -> data tetap ada
- Provider tanpa usage -> prompt_tokens, completion_tokens, est_cost_idr bernilai NULL (None)
- arguments_json & output_digest dipotong maksimal 2000 karakter
"""

import json
from types import SimpleNamespace

from httpx import ASGITransport, AsyncClient
import pytest
from sqlalchemy import select

from app.core.database import AsyncSessionLocal, init_db
from app.main import app
from app.models.agent_run import AgentRun, AgentStep
from app.services.deepseek_agent import (
    AnalyticsSalesOverviewTool,
    AnalyticsTopProductsTool,
    DeepSeekAgentService,
)


@pytest.mark.asyncio
async def test_agent_trace_two_steps_persisted_across_sessions_and_endpoint(monkeypatch):
    await init_db()

    async def _fake_overview(self, config, inputs, context):
        return {"total_gmv": 2500000, "big_blob": "X" * 3000}

    async def _fake_top(self, config, inputs, context):
        return {"items": [{"menu": "Kopi Susu", "qty": 50}]}

    monkeypatch.setattr(AnalyticsSalesOverviewTool, "execute", _fake_overview)
    monkeypatch.setattr(AnalyticsTopProductsTool, "execute", _fake_top)

    step_counter = {"n": 0}

    class _TwoStepUsageCompletions:
        async def create(self, **kwargs):
            step_counter["n"] += 1
            if step_counter["n"] == 1:
                tc = SimpleNamespace(
                    id="call_trace_1",
                    function=SimpleNamespace(
                        name="analytics__get_sales_overview",
                        arguments=json.dumps({"start_date": "2026-10-01", "end_date": "2026-10-12"}),
                    ),
                )
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content="", tool_calls=[tc]))],
                    usage=SimpleNamespace(prompt_tokens=120, completion_tokens=30),
                )
            if step_counter["n"] == 2:
                tc = SimpleNamespace(
                    id="call_trace_2",
                    function=SimpleNamespace(
                        name="analytics__get_top_products",
                        arguments=json.dumps({"start_date": "2026-10-01", "end_date": "2026-10-12"}),
                    ),
                )
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content="", tool_calls=[tc]))],
                    usage=SimpleNamespace(prompt_tokens=180, completion_tokens=40),
                )
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="Ringkasan trace selesai.", tool_calls=None))],
                usage=SimpleNamespace(prompt_tokens=200, completion_tokens=50),
            )

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=_TwoStepUsageCompletions()))
    monkeypatch.setattr(
        DeepSeekAgentService,
        "_get_active_providers",
        classmethod(lambda cls: [{"name": "FakeLLM", "client": fake_client, "model": "fake-model"}]),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.post(
            "/api/v1/agent/chat",
            json={"outlet_id": 1, "message": "Cek omzet dan menu terlaris."},
        )
        assert res.status_code == 200
        run_id = res.json()["run_id"]
        assert run_id

    # Session baru (simulasi restart)
    async with AsyncSessionLocal() as fresh_session:
        runs = list((await fresh_session.execute(select(AgentRun).where(AgentRun.run_id == run_id))).scalars().all())
        assert len(runs) == 1
        run_row = runs[0]
        assert run_row.status == "DONE"
        assert run_row.provider == "FakeLLM"
        assert run_row.model == "fake-model"
        assert run_row.steps_taken == 3
        assert run_row.tool_calls_count == 2
        assert run_row.prompt_tokens == 500
        assert run_row.completion_tokens == 120
        assert run_row.est_cost_idr is not None and run_row.est_cost_idr > 0
        assert run_row.trigger == "chat"

        steps = list(
            (await fresh_session.execute(select(AgentStep).where(AgentStep.run_id == run_id).order_by(AgentStep.id.asc())))
            .scalars()
            .all()
        )
        assert len(steps) == 2
        assert steps[0].tool_name == "analytics.get_sales_overview"
        assert steps[1].tool_name == "analytics.get_top_products"
        assert len(steps[0].output_digest or "") <= 2000

    # Endpoint read-only GET /api/v1/agent/runs/{run_id}
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res_get = await ac.get(f"/api/v1/agent/runs/{run_id}")
        assert res_get.status_code == 200
        trace_data = res_get.json()
        assert trace_data["run_id"] == run_id
        assert len(trace_data["steps"]) == 2
        assert trace_data["prompt_tokens"] == 500
        assert trace_data["completion_tokens"] == 120

        res_404 = await ac.get("/api/v1/agent/runs/run_tidak_ada")
        assert res_404.status_code == 404


@pytest.mark.asyncio
async def test_agent_trace_without_usage_stores_null_tokens_and_cost(monkeypatch):
    await init_db()

    class _NoUsageCompletions:
        async def create(self, **kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="Halo Juragan!", tool_calls=None))]
            )

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=_NoUsageCompletions()))
    monkeypatch.setattr(
        DeepSeekAgentService,
        "_get_active_providers",
        classmethod(lambda cls: [{"name": "FakeNoUsage", "client": fake_client, "model": "fake-no-usage"}]),
    )

    async with AsyncSessionLocal() as session:
        resp = await DeepSeekAgentService.chat(
            session=session,
            user_message="Halo",
            outlet_id=1,
            trigger="telegram",
        )
        run_id = resp.run_id
        assert run_id

    async with AsyncSessionLocal() as fresh_session:
        run_row = (await fresh_session.execute(select(AgentRun).where(AgentRun.run_id == run_id))).scalar_one()
        assert run_row.prompt_tokens is None
        assert run_row.completion_tokens is None
        assert run_row.est_cost_idr is None
        assert run_row.trigger == "telegram"
