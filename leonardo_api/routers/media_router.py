"""
routers/media_router.py — Phase 2.1: 動画/サムネイル取得エンドポイント

POST /{device_id}/events/{event_id}/video  - 動画アップロード
GET  /events/{event_id}/video              - 動画取得（ダッシュボード用）
GET  /events/{event_id}/thumbnail          - サムネイル取得（ダッシュボード用）
"""

import logging
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
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

    # event_id 存在確認（device_id紐付けチェック込み）
    evt = await db.execute(
        select(DetectionEvent.event_id).where(
            DetectionEvent.event_id == event_id,
            DetectionEvent.device_id == device_id,
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
            content={"status": "duplicate", "event_id": event_id},
        )

    # ヘッダ取得
    client_sha = request.headers.get("X-SHA256", "")
    codec = request.headers.get("X-Codec", "h265")
    resolution = request.headers.get("X-Resolution", "480p")
    duration_raw = request.headers.get("X-Duration-Sec")
    duration_sec = float(duration_raw) if duration_raw else None

    if not client_sha:
        raise HTTPException(status_code=400, detail="X-SHA256 header required")

    # ボディ読み込み
    video_data = await request.body()
    if len(video_data) == 0:
        raise HTTPException(status_code=400, detail="empty body")
    if len(video_data) > MAX_VIDEO_SIZE:
        return JSONResponse(
            status_code=413,
            content={"error": "file_too_large", "max_bytes": MAX_VIDEO_SIZE},
        )

    # 保存 + SHA-256検証
    result = await save_video(
        db=db,
        event_id=event_id,
        device_id=device_id,
        video_data=video_data,
        client_sha256=client_sha,
        codec=codec,
        resolution=resolution,
        duration_sec=duration_sec,
    )

    if "error" in result:
        code = 400 if result["error"] == "hash_mismatch" else 413
        return JSONResponse(status_code=code, content=result)

    await db.commit()

    return JSONResponse(
        status_code=201,
        content={"status": "uploaded", "event_id": event_id},
    )


# =============================================================================
# GET /{device_id}/events/{event_id}/video — 動画取得（ダッシュボード用）
# =============================================================================

@router.get(
    "/{device_id}/events/{event_id}/video",
    summary="動画取得",
)
async def get_video(
    device_id: str,
    event_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    token: str | None = Query(default=None),
    x_api_token: str | None = Header(default=None),
):
    # ?token= クエリパラメータ、または X-Api-Token ヘッダーで認証
    raw_token = token or x_api_token
    if raw_token is None:
        raise HTTPException(status_code=401, detail="認証トークンが必要です")
    result = await db.execute(select(Device).where(Device.api_token == raw_token))
    device = result.scalar_one_or_none()
    if device is None:
        raise HTTPException(status_code=401, detail="デバイストークンが無効です")
    if device.device_id != device_id:
        raise HTTPException(status_code=403, detail="device_id mismatch")

    rel_path = await get_media_path(db, event_id, "video")
    if rel_path is None:
        raise HTTPException(status_code=404, detail="video not found")
    full_path = Path(MEDIA_STORAGE_PATH) / rel_path
    if not full_path.exists():
        raise HTTPException(status_code=404, detail="video file missing")
    return FileResponse(str(full_path), media_type="video/mp4")


# =============================================================================
# GET /events/{event_id}/thumbnail — サムネイル取得（ダッシュボード用）
# =============================================================================

@router.get(
    "/events/{event_id}/thumbnail",
    summary="サムネイル取得",
)
async def get_thumbnail(
    event_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    rel_path = await get_media_path(db, event_id, "thumbnail")
    if rel_path is None:
        raise HTTPException(status_code=404, detail="thumbnail not found")
    full_path = Path(MEDIA_STORAGE_PATH) / rel_path
    if not full_path.exists():
        raise HTTPException(status_code=404, detail="thumbnail file missing")
    return FileResponse(str(full_path), media_type="image/jpeg")