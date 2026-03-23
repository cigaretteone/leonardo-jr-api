"""
services/media_service.py — Phase 2.1: メディアファイル管理

サムネイル保存、動画アップロード、SHA-256検証を担当。
"""

import base64
import hashlib
import logging
import os
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import (
    MEDIA_STORAGE_PATH,
    THUMBNAIL_SUBDIR,
    VIDEO_SUBDIR,
    MAX_VIDEO_SIZE,
)
from ..models import EventMedia

logger = logging.getLogger(__name__)


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def save_thumbnail(
    db: AsyncSession,
    event_id: str,
    device_id: str,
    thumbnail_b64: str,
) -> str | None:
    """Base64サムネイルをデコードしてファイル保存 + event_media INSERT"""
    try:
        image_data = base64.b64decode(thumbnail_b64)
    except Exception:
        logger.warning("Invalid base64 thumbnail for event %s", event_id)
        return None

    sha = _sha256(image_data)
    rel_path = f"{THUMBNAIL_SUBDIR}/{device_id}/{event_id}.jpg"
    full_path = Path(MEDIA_STORAGE_PATH) / THUMBNAIL_SUBDIR / device_id
    _ensure_dir(full_path)
    file_path = full_path / f"{event_id}.jpg"
    file_path.write_bytes(image_data)

    stmt = (
        insert(EventMedia)
        .values(
            event_id=event_id,
            media_type="thumbnail",
            upload_status="completed",
            codec="jpeg",
            resolution="320x240",
            file_size_bytes=len(image_data),
            sha256_hash=sha,
            storage_path=rel_path,
            uploaded_at=None,  # will use created_at
        )
        .on_conflict_do_nothing(constraint="uq_event_media")
    )
    await db.execute(stmt)
    logger.info("Thumbnail saved: %s (%d bytes)", rel_path, len(image_data))
    return rel_path


async def create_pending_video(
    db: AsyncSession,
    event_id: str,
) -> None:
    """video_requested=true 時に pending レコードを作成"""
    stmt = (
        insert(EventMedia)
        .values(
            event_id=event_id,
            media_type="video",
            upload_status="pending",
        )
        .on_conflict_do_nothing(constraint="uq_event_media")
    )
    await db.execute(stmt)


async def save_video(
    db: AsyncSession,
    event_id: str,
    device_id: str,
    video_data: bytes,
    client_sha256: str,
    codec: str = "h265",
    resolution: str = "480p",
    duration_sec: float | None = None,
) -> dict:
    """動画ファイル保存 + SHA-256検証 + event_media UPDATE"""

    # サイズチェック
    if len(video_data) > MAX_VIDEO_SIZE:
        return {"error": "file_too_large", "max_bytes": MAX_VIDEO_SIZE}

    # SHA-256検証
    server_sha = _sha256(video_data)
    if server_sha != client_sha256.lower():
        return {"error": "hash_mismatch"}

    # ファイル保存
    rel_path = f"{VIDEO_SUBDIR}/{device_id}/{event_id}.mp4"
    full_path = Path(MEDIA_STORAGE_PATH) / VIDEO_SUBDIR / device_id
    _ensure_dir(full_path)
    file_path = full_path / f"{event_id}.mp4"
    file_path.write_bytes(video_data)

    # event_media UPDATE (pending -> completed)
    from datetime import datetime, timezone
    stmt = (
        update(EventMedia)
        .where(
            EventMedia.event_id == event_id,
            EventMedia.media_type == "video",
        )
        .values(
            upload_status="completed",
            codec=codec,
            resolution=resolution,
            duration_sec=duration_sec,
            file_size_bytes=len(video_data),
            sha256_hash=server_sha,
            storage_path=rel_path,
            uploaded_at=datetime.now(timezone.utc),
        )
    )
    result = await db.execute(stmt)

    # pending レコードが無い場合は新規INSERT
    if result.rowcount == 0:
        ins = (
            insert(EventMedia)
            .values(
                event_id=event_id,
                media_type="video",
                upload_status="completed",
                codec=codec,
                resolution=resolution,
                duration_sec=duration_sec,
                file_size_bytes=len(video_data),
                sha256_hash=server_sha,
                storage_path=rel_path,
            )
            .on_conflict_do_nothing(constraint="uq_event_media")
        )
        await db.execute(ins)

    logger.info("Video saved: %s (%d bytes, sha=%s)", rel_path, len(video_data), server_sha[:16])
    return {"status": "uploaded", "storage_path": rel_path}


async def get_media_path(
    db: AsyncSession,
    event_id: str,
    media_type: str,
) -> str | None:
    """event_id + media_type からファイルパスを取得"""
    stmt = select(EventMedia.storage_path).where(
        EventMedia.event_id == event_id,
        EventMedia.media_type == media_type,
        EventMedia.upload_status == "completed",
    )
    result = await db.execute(stmt)
    row = result.scalar_one_or_none()
    return row