"""
tests/test_asset_renderer.py
T23 — Menguji asset pipeline SVG -> JPEG 1080x1080 -> storage publik:
- Render fixture SVG -> JPEG 1080x1080, quality 90, <=8MB
- SVG rusak -> AssetRenderError jelas
- Sandbox -> render lokal, public_url=None
- Non-sandbox tanpa konfigurasi storage -> STORAGE_NOT_CONFIGURED
- Non-sandbox dengan storage mock -> READY + HEAD 200 image/jpeg
"""

import pytest

from app.services.asset_renderer import (
    MAX_INSTAGRAM_IMAGE_BYTES,
    AssetRenderError,
    StorageAdapter,
    prepare_instagram_asset,
)

SAMPLE_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1080 1080" width="1080" height="1080">
  <rect x="0" y="0" width="1080" height="1080" fill="#1e293b"/>
  <circle cx="540" cy="420" r="180" fill="#f59e0b"/>
  <text x="120" y="760" fill="#ffffff">Kopi Susu Gula Aren</text>
</svg>"""


@pytest.mark.asyncio
async def test_render_svg_to_jpeg_in_sandbox(tmp_path):
    res = await prepare_instagram_asset(
        SAMPLE_SVG,
        "promo_kopi.svg",
        sandbox=True,
        output_dir=tmp_path,
    )
    assert res.status == "SANDBOX_LOCAL"
    assert res.public_url is None
    assert res.width == 1080
    assert res.height == 1080
    assert 0 < res.size_bytes <= MAX_INSTAGRAM_IMAGE_BYTES
    assert res.local_path.endswith(".jpg")


@pytest.mark.asyncio
async def test_corrupt_svg_raises_clear_error(tmp_path):
    with pytest.raises(AssetRenderError, match="SVG rusak"):
        await prepare_instagram_asset(
            "<svg><broken>",
            "broken.svg",
            sandbox=True,
            output_dir=tmp_path,
        )

    with pytest.raises(AssetRenderError, match="bukan SVG"):
        await prepare_instagram_asset(
            "<html><body>Not SVG</body></html>",
            "html.svg",
            sandbox=True,
            output_dir=tmp_path,
        )


@pytest.mark.asyncio
async def test_non_sandbox_without_storage_returns_not_configured(tmp_path):
    empty_adapter = StorageAdapter(endpoint="", bucket="", token="", public_base_url="")
    res = await prepare_instagram_asset(
        SAMPLE_SVG,
        "promo_nocfg.svg",
        sandbox=False,
        output_dir=tmp_path,
        storage=empty_adapter,
    )
    assert res.status == "STORAGE_NOT_CONFIGURED"
    assert res.public_url is None


@pytest.mark.asyncio
async def test_non_sandbox_uploads_and_verifies_head(tmp_path, monkeypatch):
    uploaded = []

    async def _fake_upload(upload_url: str, token: str, payload: bytes) -> None:
        uploaded.append((upload_url, len(payload)))

    async def _fake_head(public_url: str):
        return 200, "image/jpeg"

    monkeypatch.setattr(StorageAdapter, "_http_upload", staticmethod(_fake_upload))
    monkeypatch.setattr(StorageAdapter, "_http_head", staticmethod(_fake_head))

    adapter = StorageAdapter(
        endpoint="https://s3.example.test",
        bucket="juragan-assets",
        token="tok-123",
        public_base_url="https://cdn.example.test",
    )
    res = await prepare_instagram_asset(
        SAMPLE_SVG,
        "promo_ready.svg",
        sandbox=False,
        output_dir=tmp_path,
        storage=adapter,
    )
    assert res.status == "READY"
    assert res.public_url == "https://cdn.example.test/campaigns/promo_ready.jpg"
    assert len(uploaded) == 1
