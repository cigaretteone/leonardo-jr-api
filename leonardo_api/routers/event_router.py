"""
routers/event_router.py — 検知イベント受信・デバイスステータス・オフラインログ

POST /api/v1/devices/{device_id}/event        - 検知イベント受信（APIトークン認証）
GET  /api/v1/devices/{device_id}/status       - デバイスステータス確認（APIトークン認証）
POST /api/v1/devices/{device_id}/upload-logs  - 圏外ログ一括アップロード（APIトークン認証）
"""

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_device_by_api_token
from ..database import get_db
from ..models import DetectionEvent, Device
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

router = APIRouter()


def _get_client_ip(request: Request) -> str | None:
    """リクエスト元 IP を取得する（リバースプロキシ越しも考慮）。"""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


@router.post(
    "/{device_id}/event",
    response_model=DetectionEventResponse,
    status_code=status.HTTP_201_CREATED,
    summary="検知イベント受信（デバイス → サーバ）",
)
async def receive_event(
    device_id: str,
    body: DetectionEventRequest,
    request: Request,
    device: Annotated[Device, Depends(get_device_by_api_token)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> DetectionEventResponse:
    """
    デバイスが AI 検知した際に送信するエンドポイント。
    X-Api-Token ヘッダーで認証する（JWT ではない）。

    処理フロー:
      1. デバイス status 確認（suspended → 503）
      2. detection_events にレコード挿入
      3. 発報 IP からジオロケーション取得 → 登録座標と距離計算
      4. 150km 超 or 都道府県不一致 → location_mismatch = TRUE → 逸脱アラート
      5. 通常の検知通知を送信
    """
    # device_id の一致確認（URL パスと api_token のデバイスが合っているか）
    if device.device_id != device_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="デバイス ID とトークンが一致しません",
        )

    # Step 1: デバイス停止確認
    if device.status == "suspended":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="このデバイスは停止中です",
        )

    # Step 2: イベントレコード挿入（IP / 距離は後から更新）
    detected_at = body.timestamp or datetime.now(timezone.utc)
    ip = _get_client_ip(request)

    event = DetectionEvent(
        device_id=device_id,
        detected_at=detected_at,
        detection_type=body.detection_type,
        confidence=body.confidence,
        ip_address=ip,
        location_mismatch=False,  # 後で更新
    )
    db.add(event)
    await db.flush()  # id を取得するために flush（commit はまだしない）

    # Step 3: 登録座標の取得 → IP ジオロケーション → 距離計算
    active_loc = await get_active_location(db, device_id)
    mismatch = False
    distance_km = None
    event_region = ""

    if active_loc and ip:
        mismatch, distance_km, event_region = await check_location_mismatch(
            registered_lat=float(active_loc.lat),
            registered_lon=float(active_loc.lon),
            registered_region="",  # 実証機ではGPS座標の逆ジオコードは省略
            event_ip=ip,
        )

    # Step 4: イベントレコードを更新
    event.ip_geolocation_region = event_region or None
    event.distance_from_registered_km = distance_km
    event.location_mismatch = mismatch

    await db.commit()
    await db.refresh(event)

    # Step 5: 通知（非ブロッキングで送信。通知失敗でもレスポンスは返す）
    try:
        await send_detection_notification(
            device.notification_target,
            device_id,
            body.detection_type,
            body.confidence,
        )
        if mismatch:
            await send_mismatch_alert(
                device.notification_target,
                device_id,
                distance_km,
                event_region,
            )
    except Exception as exc:  # noqa: BLE001
        # 通知失敗はログに記録するが、発報エンドポイント自体は成功レスポンスを返す
        import logging
        logging.getLogger(__name__).error("通知送信エラー: %s", exc)

    return DetectionEventResponse(event_id=event.id, location_mismatch=mismatch)


@router.get(
    "/{device_id}/status",
    response_model=DeviceStatusResponse,
    summary="デバイスステータス確認（圏内復帰時に呼ぶ）",
)
async def get_device_status(
    device_id: str,
    device: Annotated[Device, Depends(get_device_by_api_token)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> DeviceStatusResponse:
    """
    デバイスが LTE 圏内に復帰した際に呼び出し、現在の status を確認する。
    suspended の場合はデバイス側でアラームを停止する等の処置を行う。
    """
    if device.device_id != device_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="不正なアクセスです")

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


@router.post(
    "/{device_id}/upload-logs",
    response_model=UploadLogsResponse,
    status_code=status.HTTP_201_CREATED,
    summary="圏外ログ一括アップロード",
)
async def upload_logs(
    device_id: str,
    body: UploadLogsRequest,
    device: Annotated[Device, Depends(get_device_by_api_token)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> UploadLogsResponse:
    """
    LTE 圏外中に溜まった JSONL ログを一括で detection_events に挿入する。

    オフラインログは IP ジオロケーションを行わない
    （圏外中のタイムスタンプが不正確なため、location_mismatch 判定は省略）。
    """
    if device.device_id != device_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="不正なアクセスです")

    if device.status == "suspended":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="このデバイスは停止中です",
        )

    events = [
        DetectionEvent(
            device_id=device_id,
            detected_at=item.timestamp,
            detection_type=item.detection_type,
            confidence=item.confidence,
            location_mismatch=False,  # オフラインログは逸脱判定しない
        )
        for item in body.events
    ]

    db.add_all(events)
    await db.commit()

    return UploadLogsResponse(inserted=len(events))
