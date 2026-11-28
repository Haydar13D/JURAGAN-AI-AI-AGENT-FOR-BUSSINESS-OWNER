"""
app/services/asset_renderer.py
T23 — Asset pipeline: SVG -> JPEG (1080x1080, quality 90, <=8MB) -> upload storage publik + verifikasi HEAD.
"""

import base64
from pathlib import Path
from typing import Optional, Tuple
import xml.etree.ElementTree as ET

import httpx
from PIL import Image, ImageColor, ImageDraw, ImageFont
from pydantic import BaseModel

from app.core.config import settings

MAX_INSTAGRAM_IMAGE_BYTES = 8 * 1024 * 1024  # 8 MB
TARGET_WIDTH = 1080
TARGET_HEIGHT = 1080
JPEG_QUALITY = 90
DEFAULT_ASSET_DIR = Path("app/static/campaigns/rendered")


class AssetRenderError(ValueError):
    """Dilempar ketika SVG rusak atau hasil render tidak memenuhi spesifikasi Instagram."""
    pass


class AssetPrepareResult(BaseModel):
    status: str  # SANDBOX_LOCAL | READY | STORAGE_NOT_CONFIGURED
    local_path: str
    public_url: Optional[str] = None
    width: int
    height: int
    size_bytes: int
    content_type: str = "image/jpeg"
    detail: str = ""


def _parse_color(raw: Optional[str], fallback: Tuple[int, int, int]) -> Tuple[int, int, int]:
    if not raw or raw in {"none", "transparent"}:
        return fallback
    try:
        rgb = ImageColor.getrgb(raw.strip())
        return rgb[:3]
    except Exception:
        return fallback


def _parse_float(raw: Optional[str], default: float = 0.0) -> float:
    if raw is None:
        return default
    cleaned = raw.strip().rstrip("px%")
    try:
        return float(cleaned)
    except ValueError:
        return default


def render_svg_to_jpeg(
    svg_source: str,
    output_path: Path,
    *,
    width: int = TARGET_WIDTH,
    height: int = TARGET_HEIGHT,
    quality: int = JPEG_QUALITY,
) -> Path:
    """Memvalidasi XML SVG dan me-render ke JPEG 1080x1080 berkualitas 90."""
    if not svg_source or not svg_source.strip():
        raise AssetRenderError("Konten SVG kosong.")

    try:
        root = ET.fromstring(svg_source.strip())
    except ET.ParseError as exc:
        raise AssetRenderError(f"SVG rusak / bukan XML valid: {exc}") from exc

    local_tag = root.tag.split("}")[-1].lower()
    if local_tag != "svg":
        raise AssetRenderError(f"Dokumen bukan SVG (root tag '{local_tag}').")

    vb_raw = root.attrib.get("viewBox")
    vb_w = _parse_float(root.attrib.get("width"), float(width))
    vb_h = _parse_float(root.attrib.get("height"), float(height))
    if vb_raw:
        parts = vb_raw.replace(",", " ").split()
        if len(parts) == 4:
            vb_w = max(1.0, _parse_float(parts[2], float(width)))
            vb_h = max(1.0, _parse_float(parts[3], float(height)))

    sx = width / vb_w
    sy = height / vb_h

    img = Image.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(img)

    for elem in root.iter():
        tag = elem.tag.split("}")[-1].lower()
        if tag == "rect":
            x = _parse_float(elem.attrib.get("x"), 0.0) * sx
            y = _parse_float(elem.attrib.get("y"), 0.0) * sy
            rw = _parse_float(elem.attrib.get("width"), vb_w) * sx
            rh = _parse_float(elem.attrib.get("height"), vb_h) * sy
            fill = _parse_color(elem.attrib.get("fill"), (245, 245, 245))
            draw.rectangle([x, y, x + rw, y + rh], fill=fill)
        elif tag == "circle":
            cx = _parse_float(elem.attrib.get("cx"), vb_w / 2) * sx
            cy = _parse_float(elem.attrib.get("cy"), vb_h / 2) * sy
            r = _parse_float(elem.attrib.get("r"), 50.0) * min(sx, sy)
            fill = _parse_color(elem.attrib.get("fill"), (200, 150, 100))
            draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=fill)
        elif tag == "text":
            txt = "".join(elem.itertext()).strip()
            if txt:
                tx = _parse_float(elem.attrib.get("x"), 40.0) * sx
                ty = _parse_float(elem.attrib.get("y"), 60.0) * sy
                fill = _parse_color(elem.attrib.get("fill"), (20, 20, 20))
                font = ImageFont.load_default()
                draw.text((tx, ty), txt, fill=fill, font=font)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path, format="JPEG", quality=quality, optimize=True)
    return output_path


def validate_jpeg_for_instagram(image_path: Path) -> Tuple[int, int, int]:
    """Memvalidasi ukuran <=8MB, format JPEG, dan rasio 1:1 atau 4:5."""
    if not image_path.exists():
        raise AssetRenderError(f"File asset tidak ditemukan: {image_path}")

    size_bytes = image_path.stat().st_size
    if size_bytes <= 0 or size_bytes > MAX_INSTAGRAM_IMAGE_BYTES:
        raise AssetRenderError(
            f"Ukuran gambar {size_bytes} bytes melebihi batas Instagram ({MAX_INSTAGRAM_IMAGE_BYTES} bytes)."
        )

    with Image.open(image_path) as im:
        if im.format != "JPEG":
            raise AssetRenderError(f"Format wajib JPEG, ditemukan '{im.format}'.")
        w, h = im.size

    if h <= 0:
        raise AssetRenderError("Dimensi tinggi gambar tidak valid.")

    ratio = w / h
    is_square = abs(ratio - 1.0) <= 0.02
    is_portrait = abs(ratio - 0.8) <= 0.02
    if not (is_square or is_portrait):
        raise AssetRenderError(
            f"Rasio gambar {w}x{h} ({ratio:.2f}) tidak memenuhi 1:1 atau 4:5."
        )
    return w, h, size_bytes


class StorageAdapter:
    """Adapter upload asset ke object storage publik (S3/R2 compatible)."""

    def __init__(
        self,
        endpoint: Optional[str] = None,
        bucket: Optional[str] = None,
        token: Optional[str] = None,
        public_base_url: Optional[str] = None,
    ):
        self.endpoint = (endpoint if endpoint is not None else settings.ASSET_STORAGE_ENDPOINT) or ""
        self.bucket = (bucket if bucket is not None else settings.ASSET_STORAGE_BUCKET) or ""
        self.token = (token if token is not None else settings.ASSET_STORAGE_TOKEN) or ""
        self.public_base_url = (public_base_url if public_base_url is not None else settings.ASSET_PUBLIC_BASE_URL) or ""

    def is_configured(self) -> bool:
        return bool(
            self.endpoint.strip()
            and self.bucket.strip()
            and self.token.strip()
            and self.public_base_url.strip()
        )

    @staticmethod
    async def _http_upload(upload_url: str, token: str, payload: bytes) -> None:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.put(
                upload_url,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "image/jpeg"},
                content=payload,
            )
            resp.raise_for_status()

    @staticmethod
    async def _http_head(public_url: str) -> Tuple[int, str]:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.head(public_url)
            return resp.status_code, resp.headers.get("content-type", "")

    async def upload_and_verify(self, local_path: Path, object_key: str) -> str:
        upload_url = f"{self.endpoint.rstrip('/')}/{self.bucket.strip('/')}/{object_key.lstrip('/')}"
        public_url = f"{self.public_base_url.rstrip('/')}/{object_key.lstrip('/')}"
        await self._http_upload(upload_url, self.token, local_path.read_bytes())
        status_code, content_type = await self._http_head(public_url)
        if status_code != 200 or "image/jpeg" not in content_type.lower():
            raise AssetRenderError(
                f"Verifikasi HEAD '{public_url}' gagal (status={status_code}, content-type='{content_type}')."
            )
        return public_url


class ImgBBStorageAdapter(StorageAdapter):
    """Adapter upload JPEG langsung ke ImgBB (https://api.imgbb.com/1/upload) untuk URL HTTPS publik permanen."""

    def __init__(self, api_key: Optional[str] = None):
        super().__init__(endpoint="", bucket="", token="", public_base_url="")
        self.api_key = (api_key if api_key is not None else settings.IMGBB_API_KEY) or ""

    def is_configured(self) -> bool:
        return bool(self.api_key.strip())

    async def upload_and_verify(self, local_path: Path, object_key: str) -> str:
        if not self.is_configured():
            raise AssetRenderError("IMGBB_API_KEY belum dikonfigurasi.")
        raw_bytes = local_path.read_bytes()
        b64_img = base64.b64encode(raw_bytes).decode("ascii")
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://api.imgbb.com/1/upload",
                data={
                    "key": self.api_key.strip(),
                    "image": b64_img,
                    "name": Path(object_key).stem,
                },
            )
        if resp.status_code != 200:
            raise AssetRenderError(f"Upload ImgBB gagal (HTTP {resp.status_code}): {resp.text[:200]}")
        body = resp.json()
        public_url = ((body.get("data") or {}).get("url") or "").strip()
        if not public_url:
            raise AssetRenderError(f"ImgBB tidak mengembalikan data.url: {str(body)[:200]}")
        return public_url


async def prepare_instagram_asset(
    svg_source: str,
    filename: str,
    *,
    sandbox: Optional[bool] = None,
    output_dir: Optional[Path] = None,
    storage: Optional[StorageAdapter] = None,
) -> AssetPrepareResult:
    """Merender SVG -> JPEG 1080x1080, memvalidasi, dan mengunggah ke storage publik bila non-sandbox."""
    target_dir = output_dir or DEFAULT_ASSET_DIR
    stem = Path(filename).stem
    jpeg_path = target_dir / f"{stem}.jpg"

    render_svg_to_jpeg(svg_source, jpeg_path)
    w, h, size_bytes = validate_jpeg_for_instagram(jpeg_path)

    is_sandbox = settings.INSTAGRAM_SANDBOX if sandbox is None else sandbox
    if is_sandbox:
        return AssetPrepareResult(
            status="SANDBOX_LOCAL",
            local_path=str(jpeg_path),
            public_url=None,
            width=w,
            height=h,
            size_bytes=size_bytes,
            detail="Mode sandbox aktif: JPEG dirender lokal dengan public_url=None.",
        )

    adapter = storage or StorageAdapter()
    if not adapter.is_configured():
        return AssetPrepareResult(
            status="STORAGE_NOT_CONFIGURED",
            local_path=str(jpeg_path),
            public_url=None,
            width=w,
            height=h,
            size_bytes=size_bytes,
            detail="ASSET_STORAGE_* belum dikonfigurasi; tidak bisa mengunggah ke URL publik.",
        )

    public_url = await adapter.upload_and_verify(jpeg_path, f"campaigns/{jpeg_path.name}")
    return AssetPrepareResult(
        status="READY",
        local_path=str(jpeg_path),
        public_url=public_url,
        width=w,
        height=h,
        size_bytes=size_bytes,
        detail="Asset JPEG berhasil diunggah dan diverifikasi via HEAD 200.",
    )
