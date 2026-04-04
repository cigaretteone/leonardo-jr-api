"""
routers/event_router.py — Phase 1.1: 冪等イベント受信 + ACK設計

POST /api/v1/devices/{device_id}/event         - 検知イベント受信（冪等）
GET  /api/v1/devices/{device_id}/status        - デバイスステータス確認
POST /api/v1/devices/{device_id}/upload-logs   - オフラインログ一括アップロード

NOTE: エンドポイントURLは移行互換のため /{device_id}/event を維持。
      将来的に /api/v1/events に統一する際はルーティングのみ変更。
"""

import logging
from datetime import datetime, timezone
from typing import Annotated
from ..config import API_BASE_URL
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_device_by_api_token
from ..database import get_db
from ..models import DetectionEvent, EventDelivery, Device
from ..schemas import (
    ActiveLocation,
    DetectionEventRequest,
    DetectionEventResponse,
    DeviceStatusResponse,
    UploadLogsRequest,
    UploadLogsResponse,
)
from ..services.geolocation_service import check_location_mismatch
from ..services.location_service import get_active_location
from ..services.notification_service import (
    send_detection_notification,
    send_mismatch_alert,
)
from ..services.device_service import update_last_seen
from ..services.media_service import save_thumbnail, create_pending_video

logger = logging.getLogger(__name__)
router = APIRouter()


def _get_client_ip(request: Request) -> str | None:
    """リクエスト元IPを取得する（リバースプロキシ経由もあり）"""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


def _parse_retry_count(request: Request) -> int:
    """X-Retry-Count ヘッダからリトライ回数を取得"""
    raw = request.headers.get("X-Retry-Count", "0")
    try:
        return max(0, int(raw))
    except (ValueError, TypeError):
        return 0


# =============================================================================
# POST /{device_id}/event — Phase 1.1 冪等イベント受信
# =============================================================================

@router.post(
    "/{device_id}/event",
    response_model=DetectionEventResponse,
    # status_code は動的に 201 or 200 を返すため、ここでは設定しない
    # OpenAPI doc 用に 201 を記載
    status_code=status.HTTP_201_CREATED,
    summary="検知イベント受信（冪等: 再送安全）",
    responses={
        200: {"description": "重複イベント（既に受信済み）"},
        201: {"description": "新規イベント受理"},
        400: {"description": "ペイロード不正 / device_id不一致"},
        403: {"description": "デバイス停止中"},
    },
)
async def receive_event(
    device_id: str,
    body: DetectionEventRequest,
    request: Request,
    device: Annotated[Device, Depends(get_device_by_api_token)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """
    デバイスがAI検知した際に送信するエンドポイント。
    X-Api-Token ヘッダで認証する（JWTではない）。

    冪等性:
      INSERT ... ON CONFLICT (event_id) DO NOTHING RETURNING event_id
      - RETURNING が行を返す → 新規挿入 → 201 Created
      - RETURNING が空       → 重複     → 200 OK

    処理フロー:
      1. device_id ↔ api_token 一致確認
      2. デバイス status 確認 (suspended → 403)
      3. Heartbeat更新 (last_seen)
      4. 冪等INSERT (ON CONFLICT DO NOTHING RETURNING)
      5. 新規の場合: geolocation判定 + event_delivery作成
      6. 通知送信 (非ブロッキング)
    """

    # ── Step 1: device_id 一致確認 ──
    if device.device_id != device_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="device_id mismatch: URL and api_token do not match",
        )

    # ペイロードのdevice_idとURL pathの一致も確認
    if body.device_id != device_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="device_id mismatch: payload and URL do not match",
        )

    # ── Step 2: デバイス稼働確認 ──
    if device.status == "suspended":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="device_suspended",
        )

    # ── Step 3: Heartbeat更新 ──
    await update_last_seen(db, device_id)

    # ── Step 4: 冪等INSERT ──
    retry_count = _parse_retry_count(request)
    ip = _get_client_ip(request)
    occurred_at = body.get_occurred_at()

    stmt = (
        insert(DetectionEvent)
        .values(
            event_id=body.event_id,
            device_id=device_id,
            event_type=body.event_type,
            occurred_at=occurred_at,
            received_at=datetime.now(timezone.utc),
            detection_type=body.get_detection_type(),
            confidence=body.get_confidence(),
            ip_address=ip,
            location_mismatch=False,  # geolocationで後から更新
            payload_json=body.model_dump(mode="json"),
            latitude=body.latitude,
            longitude=body.longitude,
            altitude_m=body.altitude_m,
            fix_method=body.fix_method,
            accuracy_m=body.accuracy_m,
            location_source=body.location_source,
            time_source=body.time_source,
            time_confidence=body.time_confidence,
        )
        .on_conflict_do_nothing(index_elements=["event_id"])
        .returning(DetectionEvent.event_id)
    )

    result = await db.execute(stmt)
    inserted_row = result.fetchone()

    # ── 重複の場合: 200 OK で即返却 ──
    if inserted_row is None:
        logger.info(
            "Duplicate event ignored: event_id=%s device=%s retry=%d",
            body.event_id, device_id, retry_count,
        )
        await db.commit()
        return JSONResponse(
            status_code=200,
            content={
                "event_id": str(body.event_id),
                "status": "duplicate",
                "location_mismatch": False,
                "video_requested": False,
                "upload_url": None,
            },
        )


    # ── 新規挿入の場合 ──
    logger.info(
        "New event accepted: event_id=%s device=%s retry=%d",
        body.event_id, device_id, retry_count,
    )

    # ── Step 5a: event_delivery 記録 ──
    delivery_stmt = insert(EventDelivery).values(
        event_id=body.event_id,
        state="received",
        retry_count=retry_count,
        acked_at=datetime.now(timezone.utc),
    )
    await db.execute(delivery_stmt)

    # ── Step 5b: Geolocation 判定（新規イベントのみ） ──
    mismatch = False
    distance_km = None
    event_region = ""

    active_loc = await get_active_location(db, device_id)
    if active_loc and ip:
        try:
            mismatch, distance_km, event_region = await check_location_mismatch(
                registered_lat=float(active_loc.lat),
                registered_lon=float(active_loc.lon),
                registered_region="",
                event_ip=ip,
            )
        except Exception as exc:
            logger.warning("Geolocation check failed: %s", exc)

    # geolocation結果をイベントに反映（直接UPDATE）
    if mismatch or distance_km is not None:
        from sqlalchemy import update
        update_stmt = (
            update(DetectionEvent)
            .where(DetectionEvent.event_id == body.event_id)
            .values(
                ip_geolocation_region=event_region or None,
                distance_from_registered_km=distance_km,
                location_mismatch=mismatch,
            )
        )
        await db.execute(update_stmt)

    # ── Step 5c: サムネイル保存 (Phase 2.1) ──
    thumbnail_b64 = getattr(body, 'thumbnail_b64', None)
    if thumbnail_b64:
        await save_thumbnail(db, str(body.event_id), device_id, thumbnail_b64)

    # ── Step 5d: 動画リクエスト判定 (Phase 2.1: 常にtrue) ──
    video_requested = True
    if video_requested:
        await create_pending_video(db, str(body.event_id))

    await db.commit()

    # ── Step 6: 通知（非ブロッキング、失敗してもレスポンスは返す） ──
    try:
        await send_detection_notification(
            device.notification_target,
            device_id,
            body.get_detection_type(),
            body.get_confidence(),
        )
        if mismatch:
            await send_mismatch_alert(
                device.notification_target,
                device_id,
                distance_km,
                event_region,
            )
    except Exception as exc:
        logger.error("Notification error: %s", exc)

    # ── 201 Created (Phase 2.1: video_requested追加) ──
    upload_url = f"{API_BASE_URL}/api/v1/devices/{device_id}/events/{body.event_id}/video"
    return JSONResponse(
        status_code=201,
        content={
            "event_id": str(body.event_id),
            "status": "accepted",
            "location_mismatch": mismatch,
            "video_requested": video_requested,
            "upload_url": upload_url if video_requested else None,
        },
    )

# =============================================================================
# GET /{device_id}/events — Phase 2.4: Event listing for dashboard
# =============================================================================

@router.get(
    "/{device_id}/events",
    summary="Detection events list",
    status_code=status.HTTP_200_OK,
)
async def list_events(
    device_id: str,
    device: Annotated[Device, Depends(get_device_by_api_token)],
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: int = 50,
):
    if device.device_id != device_id:
        raise HTTPException(status_code=400, detail="device_id mismatch")

    from sqlalchemy import select
    from ..models import DetectionEvent, EventMedia

    stmt = (
        select(DetectionEvent)
        .where(DetectionEvent.device_id == device_id)
        .order_by(DetectionEvent.occurred_at.desc())
        .limit(min(limit, 100))
    )
    result = await db.execute(stmt)
    events = result.scalars().all()

    out = []
    for ev in events:
        media_stmt = select(EventMedia).where(EventMedia.event_id == ev.event_id)
        media_result = await db.execute(media_stmt)
        media_rows = media_result.scalars().all()

        media_list = []
        for m in media_rows:
            media_list.append({
                "media_type": m.media_type,
                "upload_status": m.upload_status,
                "codec": m.codec,
                "file_size_bytes": m.file_size_bytes,
            })

        out.append({
            "event_id": str(ev.event_id),
            "device_id": ev.device_id,
            "event_type": ev.event_type,
            "detection_type": ev.detection_type,
            "confidence": float(ev.confidence) if ev.confidence else None,
            "occurred_at": ev.occurred_at.isoformat() if ev.occurred_at else None,
            "received_at": ev.received_at.isoformat() if ev.received_at else None,
            "location_mismatch": ev.location_mismatch,
            "media": media_list,
        })

    return {"events": out, "count": len(out)}


# =============================================================================
# DELETE /{device_id}/events/{event_id} — Delete single event + media
# =============================================================================
@router.delete(
    "/{device_id}/events/{event_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete event and associated media",
)
async def delete_event(
    device_id: str,
    event_id: str,
    device: Annotated[Device, Depends(get_device_by_api_token)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    if device.device_id != device_id:
        raise HTTPException(status_code=400, detail="device_id mismatch")

    from pathlib import Path
    from sqlalchemy import select, delete as sql_delete
    from ..config import MEDIA_STORAGE_PATH

    # Check event exists
    evt_stmt = select(DetectionEvent).where(
        DetectionEvent.event_id == event_id,
        DetectionEvent.device_id == device_id,
    )
    evt_result = await db.execute(evt_stmt)
    evt = evt_result.scalar_one_or_none()
    if evt is None:
        raise HTTPException(status_code=404, detail="event_not_found")

    # Delete media files from disk
    media_stmt = select(EventMedia).where(EventMedia.event_id == event_id)
    media_result = await db.execute(media_stmt)
    media_rows = media_result.scalars().all()
    for m in media_rows:
        if m.storage_path:
            fpath = Path(MEDIA_STORAGE_PATH) / m.storage_path
            if fpath.exists():
                fpath.unlink()

    # Delete media records
    await db.execute(sql_delete(EventMedia).where(EventMedia.event_id == event_id))

    # Delete event delivery records if they exist
    try:
        from ..models import EventDelivery
        await db.execute(sql_delete(EventDelivery).where(EventDelivery.event_id == event_id))
    except Exception:
        pass

    # Delete event record
    await db.execute(sql_delete(DetectionEvent).where(DetectionEvent.event_id == event_id))
    await db.commit()

    return None

# =============================================================================
# GET /{device_id}/status (変更なし)
# =============================================================================

@router.get(
    "/{device_id}/status",
    response_model=DeviceStatusResponse,
    summary="デバイスステータス確認",
)
async def get_device_status(
    device_id: str,
    device: Annotated[Device, Depends(get_device_by_api_token)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> DeviceStatusResponse:
    if device.device_id != device_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="不正なアクセスです",
        )

    await update_last_seen(db, device_id)
    active_loc = await get_active_location(db, device_id)

    return DeviceStatusResponse(
        status=device.status,
        active_location=ActiveLocation(
            lat=float(active_loc.lat),
            lon=float(active_loc.lon),
            accuracy=float(active_loc.accuracy) if active_loc.accuracy else None,
            registered_at=active_loc.registered_at,
        )
        if active_loc
        else None,
    )


# =============================================================================
# POST /{device_id}/upload-logs (変更なし)
# =============================================================================

@router.post(
    "/{device_id}/upload-logs",
    response_model=UploadLogsResponse,
    status_code=status.HTTP_201_CREATED,
    summary="オフラインログ一括アップロード",
)
async def upload_logs(
    device_id: str,
    body: UploadLogsRequest,
    device: Annotated[Device, Depends(get_device_by_api_token)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> UploadLogsResponse:
    if device.device_id != device_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="不正なアクセスです",
        )

    if device.status == "suspended":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="このデバイスは稼働停止中です",
        )

    await update_last_seen(db, device_id)

    import uuid as _uuid
    now = datetime.now(timezone.utc)
    events = []
    deliveries = []
    for item in body.events:
        eid = _uuid.uuid4()
        events.append(
            DetectionEvent(
                event_id=eid,
                device_id=device_id,
                event_type="detection",
                occurred_at=item.timestamp,
                received_at=now,
                detection_type=item.detection_type,
                confidence=item.confidence,
                location_mismatch=False,
            )
        )
        deliveries.append(
            EventDelivery(
                event_id=eid,
                state="received",
                retry_count=0,
                acked_at=now,
            )
        )

    db.add_all(events)
    await db.flush()
    db.add_all(deliveries)
    await db.commit()

    return UploadLogsResponse(inserted=len(events))
