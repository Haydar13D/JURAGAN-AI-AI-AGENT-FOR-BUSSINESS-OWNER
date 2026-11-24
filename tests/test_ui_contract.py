"""
tests/test_ui_contract.py
Contract tests untuk mencegah bug frontend/UI lolos tanpa terdeteksi:
1. Validasi syntax JavaScript (node --check) pada template HTML utama.
2. Verifikasi semua rute UI terpadu & aset CSS Hallmark (Coral) mengembalikan 200 OK.
3. Verifikasi kontrak end-to-end API workflow yang dipanggil oleh unified_app.html.
"""

from pathlib import Path
import re
import shutil
import subprocess
import tempfile

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize(
    "template_rel",
    [
        "app/templates/unified_app.html",
        "app/templates/kirata_builder.html",
    ],
)
def test_ui_templates_have_valid_js_syntax(template_rel):
    node_bin = shutil.which("node")
    assert node_bin is not None, "Node.js diperlukan untuk validasi syntax JS template UI."

    html_path = ROOT / template_rel
    html = html_path.read_text(encoding="utf-8")
    scripts = re.findall(r"<script[^>]*>([\s\S]*?)</script>", html)
    inline_js = "\n".join(s for s in scripts if s.strip())
    assert inline_js.strip(), f"Tidak ditemukan inline script di {template_rel}"

    with tempfile.NamedTemporaryFile(suffix=".js", delete=False, mode="w", encoding="utf-8") as tmp:
        tmp.write(inline_js)
        tmp_path = Path(tmp.name)

    try:
        proc = subprocess.run([node_bin, "--check", str(tmp_path)], capture_output=True, text=True)
        assert proc.returncode == 0, f"JS SyntaxError di {template_rel}:\n{proc.stderr}"
    finally:
        tmp_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_unified_ui_routes_and_static_assets_serve_200():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        for path in ("/", "/dashboard"):
            res = await ac.get(path)
            assert res.status_code == 200, f"{path} gagal: {res.status_code}"
            assert "/static/ui/app.css" in res.text
            assert "renderSafeMarkdown" in res.text

        for legacy_path in ("/kirata", "/juragan"):
            res = await ac.get(legacy_path)
            assert res.status_code == 200, f"{legacy_path} gagal: {res.status_code}"

        for asset in ("/static/ui/app.css", "/static/ui/tokens.css", "/static/ui/base.css"):
            res = await ac.get(asset)
            assert res.status_code == 200, f"Asset {asset} gagal: {res.status_code}"


@pytest.mark.asyncio
async def test_workflow_ui_contract_end_to_end():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        tpl_res = await ac.get("/api/v1/workflows/templates")
        assert tpl_res.status_code == 200
        assert len(tpl_res.json()) >= 2

        tpl_id = "tpl_used_iphone_pricing_v1"
        sample_res = await ac.get(f"/api/v1/workflows/templates/{tpl_id}/sample-data")
        assert sample_res.status_code == 200
        sample_csv = sample_res.json().get("content", "")
        assert len(sample_csv) > 100

        create_res = await ac.post(
            "/api/v1/workflows/from-template",
            json={
                "template_id": tpl_id,
                "tenant_id": "tenant_ui_contract",
                "name": "UI Contract Check WF",
                "onboarding": {},
            },
        )
        assert create_res.status_code == 200
        wf_id = create_res.json()["workflow"]["id"]

        ver_res = await ac.get(f"/api/v1/workflows/{wf_id}/versions/1")
        assert ver_res.status_code == 200
        assert len(ver_res.json()["nodes"]) >= 1

        dry_res = await ac.post(
            f"/api/v1/workflows/{wf_id}/dry-run?version=1",
            json={"csv_content": sample_csv},
        )
        assert dry_res.status_code == 200
        assert dry_res.json().get("is_clean") is True

        approve_res = await ac.post(f"/api/v1/workflows/{wf_id}/approve?version=1")
        assert approve_res.status_code == 200
        assert approve_res.json().get("status") == "SUCCESS"

        run_res = await ac.post(
            f"/api/v1/workflows/{wf_id}/runs",
            json={"csv_content": sample_csv},
        )
        assert run_res.status_code == 200
        run_data = run_res.json()
        assert run_data.get("status") == "WAITING_APPROVAL"
        pa = run_data.get("pending_action") or {}
        action_id = pa.get("action_id") or pa.get("id")
        assert action_id

        dec_res = await ac.post(
            f"/api/v1/workflows/runs/{run_data['run_id']}/actions/{action_id}/decision",
            json={"decision": "APPROVE", "reviewer_user_id": "owner"},
        )
        assert dec_res.status_code == 200
        assert dec_res.json().get("status") == "APPROVED_AND_COMPLETED"
