"""
app/services/instagram_api.py
T24 — Client Meta Instagram Graph API dengan container polling (timeout 60s) dan error taxonomy eksplisit.
"""

import asyncio
from enum import Enum
import time
from typing import Any, Dict, Optional

import httpx

from app.core.config import settings

GRAPH_API_BASE = "https://graph.facebook.com/v21.0"


class InstagramErrorCode(str, Enum):
    TOKEN_EXPIRED = "TOKEN_EXPIRED"
    MEDIA_INVALID = "MEDIA_INVALID"
    RATE_LIMITED = "RATE_LIMITED"
    API_ERROR = "API_ERROR"


class InstagramApiError(RuntimeError):
    """Error terstruktur dari Instagram Graph API dengan kode & pesan actionable."""

    def __init__(self, code: InstagramErrorCode, actionable_message: str):
        super().__init__(actionable_message)
        self.code = code
        self.actionable_message = actionable_message


def map_instagram_http_error(status_code: int, payload: Any) -> InstagramApiError:
    err_obj = payload.get("error", {}) if isinstance(payload, dict) else {}
    err_code = err_obj.get("code")
    err_msg = str(err_obj.get("message") or payload or f"HTTP {status_code}")
    lowered = err_msg.lower()

    if status_code == 401 or err_code == 190 or "expired" in lowered or "oauth" in lowered or "invalid token" in lowered:
        return InstagramApiError(
            InstagramErrorCode.TOKEN_EXPIRED,
            f"TOKEN_EXPIRED: Token Instagram kedaluwarsa atau tidak valid ({err_msg}). Perbarui INSTAGRAM_ACCESS_TOKEN di .env.",
        )
    if status_code == 429 or err_code in {4, 17, 32, 613} or "rate limit" in lowered:
        return InstagramApiError(
            InstagramErrorCode.RATE_LIMITED,
            f"RATE_LIMITED: Batas panggilan Instagram API terlampaui ({err_msg}). Coba lagi nanti.",
        )
    if err_code in {36000, 36001, 36003, 9004} or "media" in lowered or "image" in lowered or "aspect ratio" in lowered:
        return InstagramApiError(
            InstagramErrorCode.MEDIA_INVALID,
            f"MEDIA_INVALID: Media ditolak oleh Instagram ({err_msg}). Pastikan JPEG 1080x1080 dan URL publik dapat diakses.",
        )
    return InstagramApiError(
        InstagramErrorCode.API_ERROR,
        f"API_ERROR: Panggilan Instagram Graph API gagal (HTTP {status_code}: {err_msg}).",
    )


class InstagramGraphClient:
    """Client async untuk alur create media container -> poll FINISHED -> media_publish."""

    def __init__(
        self,
        access_token: Optional[str] = None,
        ig_user_id: Optional[str] = None,
        base_url: str = GRAPH_API_BASE,
    ):
        self.access_token = (access_token if access_token is not None else settings.INSTAGRAM_ACCESS_TOKEN) or ""
        self.ig_user_id = (ig_user_id if ig_user_id is not None else settings.INSTAGRAM_IG_USER_ID) or ""
        self.base_url = base_url.rstrip("/")

    def _require_credentials(self) -> None:
        if not self.access_token.strip() or not self.ig_user_id.strip():
            raise InstagramApiError(
                InstagramErrorCode.TOKEN_EXPIRED,
                "TOKEN_EXPIRED: INSTAGRAM_ACCESS_TOKEN atau INSTAGRAM_IG_USER_ID belum dikonfigurasi.",
            )

    async def _request(self, method: str, path: str, **kwargs: Any) -> Dict[str, Any]:
        self._require_credentials()
        url = f"{self.base_url}/{path.lstrip('/')}"
        params = dict(kwargs.pop("params", {}) or {})
        params["access_token"] = self.access_token
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.request(method, url, params=params, **kwargs)
        try:
            data = resp.json() if resp.content else {}
        except Exception:
            data = {"raw": resp.text}
        if resp.status_code >= 400 or (isinstance(data, dict) and "error" in data):
            raise map_instagram_http_error(resp.status_code, data)
        return data

    async def create_media_container(self, image_url: str, caption: str) -> str:
        data = await self._request(
            "POST",
            f"{self.ig_user_id}/media",
            data={"image_url": image_url, "caption": caption},
        )
        cid = data.get("id")
        if not cid:
            raise InstagramApiError(InstagramErrorCode.API_ERROR, "API_ERROR: Container ID tidak dikembalikan oleh Meta.")
        return str(cid)

    async def get_container_status(self, container_id: str) -> str:
        data = await self._request(
            "GET",
            container_id,
            params={"fields": "status_code"},
        )
        return str(data.get("status_code") or "IN_PROGRESS").upper()

    async def wait_for_container_finished(
        self,
        container_id: str,
        *,
        timeout_seconds: float = 60.0,
        poll_interval: float = 1.0,
    ) -> None:
        deadline = time.monotonic() + timeout_seconds
        while True:
            status_code = await self.get_container_status(container_id)
            if status_code == "FINISHED":
                return
            if status_code in {"ERROR", "EXPIRED"}:
                raise InstagramApiError(
                    InstagramErrorCode.MEDIA_INVALID,
                    f"MEDIA_INVALID: Status media container '{container_id}' adalah {status_code}.",
                )
            if time.monotonic() >= deadline:
                raise InstagramApiError(
                    InstagramErrorCode.API_ERROR,
                    f"API_ERROR: Polling media container '{container_id}' melewati timeout {timeout_seconds}s.",
                )
            await asyncio.sleep(poll_interval)

    async def publish_media_container(self, container_id: str) -> str:
        data = await self._request(
            "POST",
            f"{self.ig_user_id}/media_publish",
            data={"creation_id": container_id},
        )
        post_id = data.get("id")
        if not post_id:
            raise InstagramApiError(InstagramErrorCode.API_ERROR, "API_ERROR: Post ID tidak dikembalikan saat media_publish.")
        return str(post_id)
