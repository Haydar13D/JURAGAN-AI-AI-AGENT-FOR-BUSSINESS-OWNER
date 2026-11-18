"""
scripts/smoke_live.py
T15 — Smoke test end-to-end dengan API LLM nyata (tanpa fake client).
Menyimpan transkrip ke docs/progress/live-smoke-<YYYY-MM-DD>.md.
"""

import asyncio
from datetime import date, datetime
import json
from pathlib import Path
import sys
import uuid

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.core.config import settings
from app.core.database import AsyncSessionLocal, init_db
from app.services.deepseek_agent import DeepSeekAgentService, LLMUnavailableError

PROVIDER_MODEL_ENV_VAR = {
    "AtmoRouter": "ATMOROUTER_MODEL",
    "DeepSeek": "DEEPSEEK_MODEL",
    "OpenRouter": "OPENROUTER_MODEL",
}


async def _diagnose_404_env_vars(providers) -> list[str]:
    """Menguji apakah kegagalan disebabkan oleh model 404 dan mengembalikan nama variabel .env."""
    bad_vars: list[str] = []
    for prov in providers:
        try:
            await prov["client"].chat.completions.create(
                model=prov["model"],
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=1,
            )
        except Exception as exc:
            err_str = str(exc).lower()
            if "404" in err_str or "model_not_found" in err_str or "not available" in err_str:
                env_var = PROVIDER_MODEL_ENV_VAR.get(prov["name"], "ATMOROUTER_MODEL")
                bad_vars.append(f"{env_var} (model='{prov['model']}' pada {prov['name']} mengembalikan 404)")
    return bad_vars


async def main() -> int:
    providers = DeepSeekAgentService._get_active_providers()
    if not (settings.ATMOROUTER_API_KEY or "").strip() and not providers:
        print("skip: ATMOROUTER_API_KEY kosong")
        return 0

    if not providers:
        print("skip: ATMOROUTER_API_KEY kosong")
        return 0

    await init_db()
    today_str = date.today().isoformat()
    question = (
        "Berapa total omzet dan jumlah transaksi outlet ini untuk periode "
        "2026-10-01 sampai 2026-10-12?"
    )

    try:
        async with AsyncSessionLocal() as session:
            resp = await DeepSeekAgentService.chat(
                session=session,
                user_message=question,
                outlet_id=settings.DEFAULT_OUTLET_ID,
                conversation_id=f"smoke_live_{today_str}_{uuid.uuid4().hex[:6]}",
            )
    except LLMUnavailableError as exc:
        bad_vars = await _diagnose_404_env_vars(providers)
        if bad_vars:
            print(
                "ERROR 404 model tidak tersedia. Ubah variabel .env berikut: "
                + "; ".join(bad_vars)
            )
        else:
            print(f"ERROR LLM_UNAVAILABLE: {exc}")
        return 1

    if not resp.tools_executed:
        print("ERROR: Smoke test gagal — tidak ada tool call yang tereksekusi.")
        return 1

    out_dir = ROOT_DIR / "docs" / "progress"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"live-smoke-{today_str}.md"

    transcript = [
        f"# Live Smoke Test — {today_str}",
        "",
        f"- **Timestamp:** {datetime.now().isoformat(timespec='seconds')}",
        f"- **Provider:** `{resp.provider_used}`",
        f"- **Model:** `{resp.model_used}`",
        f"- **Conversation ID:** `{resp.conversation_id}`",
        f"- **Status:** `{resp.status}`",
        f"- **Tools Executed:** {', '.join(f'`{t}`' for t in resp.tools_executed)}",
        "",
        "## Pertanyaan",
        question,
        "",
        "## Tool Details",
        "```json",
        json.dumps([d.model_dump() for d in resp.tool_details], ensure_ascii=False, indent=2, default=str),
        "```",
        "",
        "## Jawaban Akhir",
        resp.reply,
        "",
    ]
    out_path.write_text("\n".join(transcript), encoding="utf-8")
    print(f"OK: Smoke test live sukses ({len(resp.tools_executed)} tool call). Transkrip tersimpan di {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
