"""
evals/run_eval.py
T16 — Eval harness untuk mengukur perilaku model dan pipeline run_agent().
Mendukung --offline (fake LLM + fixture tools tanpa network) dan --live (provider nyata).
"""

import argparse
import asyncio
from collections import Counter
from datetime import date, datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Sequence, Tuple

from pydantic import BaseModel

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.core.config import settings
from app.core.database import AsyncSessionLocal, init_db
from app.core.tool_registry import BaseTool, ExecutionContext, ToolRegistry, from_wire_name, to_wire_name
from app.services.agent_loop import AgentResult, AgentStatus, run_agent
from app.services.deepseek_agent import DeepSeekAgentService

# Pastikan seluruh tool produksi terdaftar di ToolRegistry
import app.core.governance_tools  # noqa: F401
import app.services.fnb_marketing.tools  # noqa: F401
import app.services.inventory_pack.tools  # noqa: F401
import app.services.retail_pricing.tools  # noqa: F401
from app.services.instagram_publisher import register_instagram_tool
from app.services.memory_service import register_memory_tool
from app.services.whatsapp_tool import register_whatsapp_tool
from app.services.workflow_tool import register_workflow_tool

register_workflow_tool()
register_whatsapp_tool()
register_memory_tool()
register_instagram_tool()

DEFAULT_GOLDEN_SET = ROOT_DIR / "evals" / "golden_set.jsonl"
REPORTS_DIR = ROOT_DIR / "evals" / "reports"


def _git_short_sha() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(ROOT_DIR),
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return out.strip() or "unknown"
    except Exception:
        return "unknown"


def load_golden_set(path: Path) -> List[Dict[str, Any]]:
    cases: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line_no, raw in enumerate(fh, start=1):
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                cases.append(json.loads(stripped))
            except json.JSONDecodeError as exc:
                raise ValueError(f"JSON tidak valid di {path}:{line_no}: {exc}") from exc
    return cases


def _minimal_value(spec: Dict[str, Any], defs: Dict[str, Any], field_name: str) -> Any:
    if "$ref" in spec:
        ref_name = spec["$ref"].rsplit("/", 1)[-1]
        ref_schema = defs.get(ref_name, {})
        ref_props = ref_schema.get("properties", {})
        return {
            k: _minimal_value(ref_props.get(k, {}), defs, k)
            for k in ref_schema.get("required", [])
        }
    if "default" in spec:
        return spec["default"]
    ftype = spec.get("type")
    ffmt = spec.get("format")
    if ffmt == "date" or "date" in field_name:
        return "2026-10-01" if "start" in field_name or field_name == "date" else "2026-10-12"
    if ftype == "integer":
        return 1
    if ftype == "number":
        return 1.0
    if ftype == "boolean":
        return False
    if ftype == "array":
        return []
    if ftype == "object":
        return {}
    return "sample"


def _minimal_valid_args(tool: BaseTool) -> Dict[str, Any]:
    """Menyusun argumen minimal valid dari input_schema Pydantic untuk simulasi offline."""
    schema = tool.input_schema.model_json_schema()
    props = schema.get("properties", {})
    defs = schema.get("$defs", {})
    required = schema.get("required", [])
    return {
        field_name: _minimal_value(props.get(field_name, {}), defs, field_name)
        for field_name in required
    }


class _OfflineDummyOutput(BaseModel):
    ok: bool = True


class _OfflineFixtureTool(BaseTool):
    """Pembungkus ringan untuk --offline agar tool tetap memakai schema & risk_tier asli tanpa DB/network."""

    def __init__(self, inner: BaseTool):
        self.metadata = inner.metadata
        self.config_schema = inner.config_schema
        self.input_schema = inner.input_schema
        self.output_schema = inner.output_schema
        self._inner = inner

    async def execute(self, config: BaseModel, inputs: BaseModel, context: ExecutionContext) -> BaseModel:
        return _OfflineDummyOutput(ok=True)


class _OfflineFakeCompletions:
    def __init__(self, case: Dict[str, Any], tool_by_name: Dict[str, BaseTool]):
        self._case = case
        self._tool_by_name = tool_by_name
        self._step = 0

    async def create(self, **kwargs: Any) -> Any:
        expected = list(self._case.get("expected_tools") or [])
        if self._step < len(expected):
            target_tool = expected[self._step]
            self._step += 1
            tool_obj = self._tool_by_name.get(target_tool)
            args = _minimal_valid_args(tool_obj) if tool_obj is not None else {}
            tc = SimpleNamespace(
                id=f"call_{self._case['id']}_{self._step}",
                type="function",
                function=SimpleNamespace(
                    name=to_wire_name(target_tool),
                    arguments=json.dumps(args, ensure_ascii=False),
                ),
            )
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="", tool_calls=[tc]))])

        keywords = (self._case.get("expected_facts") or {}).get("reply_keywords") or ["ok"]
        reply = f"Hasil untuk {self._case['id']}: " + ", ".join(str(k) for k in keywords)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=reply, tool_calls=None))])


def _evaluate_case(case: Dict[str, Any], res: AgentResult) -> Dict[str, Any]:
    attempted = [from_wire_name(t.tool_name) for t in res.traces]
    if res.pending_approval and res.pending_approval.get("tool_name"):
        attempted.append(from_wire_name(res.pending_approval["tool_name"]))

    expected = list(case.get("expected_tools") or [])
    forbidden = set(case.get("forbidden_tools") or [])
    attempted_set = set(attempted)

    forbidden_hits = sorted(t for t in attempted_set if t in forbidden)
    has_forbidden = len(forbidden_hits) > 0

    if expected:
        tool_ok = all(t in attempted_set for t in expected) and not has_forbidden
    else:
        tool_ok = len(attempted) == 0 and not has_forbidden

    facts = case.get("expected_facts") or {}
    fact_ok: Optional[bool] = None
    if facts:
        checks: List[bool] = []
        if "expected_status" in facts:
            checks.append(res.status.value == facts["expected_status"])
        if "reply_keywords" in facts:
            reply_lower = (res.reply or "").lower()
            checks.append(all(str(kw).lower() in reply_lower for kw in facts["reply_keywords"]))
        fact_ok = all(checks) if checks else True

    return {
        "id": case["id"],
        "category": case["category"],
        "status": res.status.value,
        "expected_tools": expected,
        "attempted_tools": attempted,
        "tool_ok": tool_ok,
        "forbidden_hits": forbidden_hits,
        "fact_ok": fact_ok,
    }


async def run_evaluation(
    mode: str,
    golden_set_path: Path,
    min_accuracy: float,
    concurrency: int = 5,
    case_timeout_seconds: float = 60.0,
) -> int:
    cases = load_golden_set(golden_set_path)
    real_tools = ToolRegistry.get_all_latest()
    tool_by_name = {t.metadata.tool_type: t for t in real_tools}

    live_provider: Optional[Dict[str, Any]] = None
    if mode == "live":
        providers = DeepSeekAgentService._get_active_providers()
        if not providers:
            print("skip: tidak ada provider LLM aktif untuk mode --live")
            return 1
        live_provider = providers[0]
        await init_db()
        eval_tools: Sequence[BaseTool] = real_tools
    else:
        eval_tools = [_OfflineFixtureTool(t) for t in real_tools]

    total = len(cases)
    sem = asyncio.Semaphore(max(1, concurrency if mode == "live" else 1))
    done_count = 0

    async def _run_one(case: Dict[str, Any]) -> Tuple[Dict[str, Any], float]:
        nonlocal done_count
        async with sem:
            t0 = time.perf_counter()
            if mode == "offline":
                fake_client = SimpleNamespace(
                    chat=SimpleNamespace(completions=_OfflineFakeCompletions(case, tool_by_name))
                )
                res = await run_agent(
                    goal=case["pertanyaan"],
                    allowed_tools=eval_tools,
                    llm_client=fake_client,
                    model="offline-fake-model",
                )
            else:
                async with AsyncSessionLocal() as session:
                    ctx = ExecutionContext(
                        tenant_id=f"outlet_{settings.DEFAULT_OUTLET_ID}",
                        workflow_id="eval_live",
                        workflow_version=1,
                        run_id=f"eval_{case['id']}",
                        actor_id="eval_harness",
                        extra_meta={"db_session": session, "outlet_id": settings.DEFAULT_OUTLET_ID},
                    )
                    try:
                        res = await asyncio.wait_for(
                            run_agent(
                                goal=case["pertanyaan"],
                                allowed_tools=eval_tools,
                                session=session,
                                llm_client=live_provider["client"],
                                model=live_provider["model"],
                                system_prompt=DeepSeekAgentService._build_system_prompt(),
                                context=ctx,
                            ),
                            timeout=case_timeout_seconds,
                        )
                    except asyncio.TimeoutError:
                        res = AgentResult(
                            status=AgentStatus.TOOL_ERROR,
                            reply=f"Timeout eval >{case_timeout_seconds}s",
                        )
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            ev = _evaluate_case(case, res)
            ev["latency_ms"] = round(elapsed_ms, 2)
            if mode == "live":
                ev["reply"] = res.reply
                done_count += 1
                print(
                    f"[{done_count}/{total}] {case['id']} ({case['category']}): "
                    f"status={ev['status']} tool_ok={ev['tool_ok']} ({elapsed_ms:.0f}ms)",
                    flush=True,
                )
            return ev, elapsed_ms

    pairs = await asyncio.gather(*(_run_one(c) for c in cases))
    case_results = [p[0] for p in pairs]
    latencies_ms = [p[1] for p in pairs]
    status_counter: Counter[str] = Counter(r["status"] for r in case_results)

    total = len(case_results)
    tool_accuracy = sum(1 for r in case_results if r["tool_ok"]) / total if total else 0.0
    forbidden_rate = sum(1 for r in case_results if r["forbidden_hits"]) / total if total else 0.0
    fact_cases = [r for r in case_results if r["fact_ok"] is not None]
    fact_match_rate = (
        sum(1 for r in fact_cases if r["fact_ok"]) / len(fact_cases) if fact_cases else 1.0
    )
    avg_latency_ms = round(sum(latencies_ms) / total, 2) if total else 0.0

    sha = _git_short_sha()
    today_str = date.today().isoformat()
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORTS_DIR / f"{today_str}-{sha}.json"

    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": mode,
        "git_sha": sha,
        "total_cases": total,
        "min_accuracy": min_accuracy,
        "tool_selection_accuracy": round(tool_accuracy, 4),
        "forbidden_violation_rate": round(forbidden_rate, 4),
        "fact_match_rate": round(fact_match_rate, 4),
        "status_distribution": dict(status_counter),
        "avg_latency_ms": avg_latency_ms,
        "avg_cost_idr": None,
        "cases": case_results,
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        f"Eval ({mode}) selesai: {total} kasus | "
        f"tool_selection_accuracy={report['tool_selection_accuracy']} | "
        f"forbidden_violation_rate={report['forbidden_violation_rate']} | "
        f"fact_match_rate={report['fact_match_rate']} | "
        f"report={report_path}"
    )

    if forbidden_rate > 0 or tool_accuracy < min_accuracy:
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="JuraganAI Eval Harness (T16)")
    mode_grp = parser.add_mutually_exclusive_group()
    mode_grp.add_argument("--offline", action="store_true", help="Jalankan eval offline tanpa network")
    mode_grp.add_argument("--live", action="store_true", help="Jalankan eval live terhadap provider LLM aktif")
    parser.add_argument("--min-accuracy", type=float, default=0.8, help="Threshold minimal tool_selection_accuracy")
    parser.add_argument("--concurrency", type=int, default=6, help="Jumlah kasus paralel pada mode --live")
    parser.add_argument("--golden-set", type=Path, default=DEFAULT_GOLDEN_SET, help="Path ke golden_set.jsonl")
    args = parser.parse_args()

    mode = "live" if args.live else "offline"
    return asyncio.run(
        run_evaluation(
            mode=mode,
            golden_set_path=args.golden_set,
            min_accuracy=args.min_accuracy,
            concurrency=args.concurrency,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
