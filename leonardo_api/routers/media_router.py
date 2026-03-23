"""
routers/media_router.py — Phase 2.1: 動画/サムネイル取得エンドポイント

POST /{device_id}/events/{event_id}/video  - 動画アップロード
GET  /events/{event_id}/video              - 動画取得（ダッシュボード用）
GET  /events/{event_id}/thumbnail          - サムネイル取得（ダッシュボード用）
"""

import logging
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_device_by_api_token
from ..config import MEDIA_STORAGE_PATH, MAX_VIDEO_SIZE
from ..database import get_db
from ..models import DetectionEvent, Device, EventMedia
from ..services.media_service import save_video, get_media_path

logger = logging.getLogger(__name__)
router = APIRouter()


# =============================================================================
# POST /{device_id}/events/{event_id}/video — 動画アップロード
# =============================================================================

@router.post(
    "/{device_id}/events/{event_id}/video",
    status_code=status.HTTP_201_CREATED,
    summary="動画アップロード",
    responses={
        200: {"description": "重複（既にアップロード済み）"},
        201: {"description": "アップロード成功"},
        400: {"description": "SHA-256不一致"},
        404: {"description": "event_id不存在"},
        413: {"description": "ファイルサイズ超過"},
    },
)
async def upload_video(
    device_id: str,
    event_id: str,
    request: Request,
    device: Annotated[Device, Depends(get_device_by_api_token)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    # デバイス認証
    if device.device_id != device_id:
        raise HTTPException(status_code=400, detail="device_id mismatch")
    if device.status == "suspended":
        raise HTTPException(status_code=403, detail="device_suspended")

    # event_id 存在確認
    evt = await db.execute(
        select(DetectionEvent.event_id).where(
            DetectionEvent.event_id == event_id
        )
    )
    if evt.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="event_not_found")

    # 重複チェック
    existing = await db.execute(
        select(EventMedia.media_id).where(
            EventMedia.event_id == event_id,
            EventMedia.media_type == "video",
            EventMedia.upload_status == "completed",
        )
    )
    if existing.scalar_one_or_none() is not None:
        return JSONResponse(
            status_code=200,
            content={"status": "duplicate"},
        )

    # ヘッダ取得
    client_sha = request.headers.get("X-SHA256", "")
    codec = request.headers.get("X-Codec", "h265")
    resolution = request.headers.get("X-Resolution", "480p")
    duration_raw = request.headers.get("X-Duration-Sec")
    duration_sec = float(duration_raw) if duration_raw else