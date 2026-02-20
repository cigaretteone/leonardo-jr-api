"""
routers/device_router.py — デバイス登録・セットアップエンドポイント

POST /api/v1/devices/{device_id}/register  - QR 読み取り後の初回登録
PUT  /api/v1/devices/{device_id}/setup     - 通知先・検知対象設定
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user
from ..database import get_db
from ..models import Device, User
from ..schemas import DeviceRegisterResponse, DeviceSetupRequest, DeviceSetupResponse
from ..services.device_service import (
    get_device_for_owner,
    register_device,
    update_device_setup,
    verify_factory_token_hash,
)

router = APIRouter()


@router.post(
    "/{device_id}/register",
    response_model=DeviceRegisterResponse,
    status_code=status.HTTP_201_CREATED,
    summary="デバイス登録（QR 読み取り後）",
)
async def register_device_endpoint(
    device_id: str,
    fth: Annotated[str, Query(description="factory_token_hash（QR の fth パラメータ）")],
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> DeviceRegisterResponse:
    """
    QR コードのセットアップ URL から遷移して呼び出される。

    処理フロー:
      1. factory_token_hash（fth）の検証
      2. devices テーブルの存在確認
         - 未存在 → この時点でレコードを作成（pre-register なし）
         - owner_user_id IS NULL → 登録可能
         - owner 設定済み → 409 Conflict
      3. owner_user_id にログインユーザーを設定
      4. api_token を発行してレスポンスに含める
    """
    # Step 1: factory_token_hash 検証
    if not verify_factory_token_hash(device_id, fth):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="デバイス認証に失敗しました（fth が一致しません）",
        )

    # Step 2: 既登録チェック
    result = await db.execute(select(Device).where(Device.device_id == device_id))
    existing = result.scalar_one_or_none()

    if existing is not None and existing.owner_user_id is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="このデバイスはすでに登録済みです。再登録には既存所有者の認証が必要です。",
        )

    # Step 3 & 4: デバイスを所有者に紐付け、api_token を発行
    device = await register_device(db, device_id, current_user.user_id)

    return DeviceRegisterResponse(
        device_id=device.device_id,
        api_token=device.api_token,
    )


@router.put(
    "/{device_id}/setup",
    response_model=DeviceSetupResponse,
    summary="デバイスセットアップ（通知先・検知対象設定）",
)
async def setup_device_endpoint(
    device_id: str,
    body: DeviceSetupRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> DeviceSetupResponse:
    """
    通知先（LINE Notify トークン / メール）と検知対象（熊 / 人 / 車両）を設定する。
    所有者のみ操作可能。
    """
    device = await get_device_for_owner(db, device_id, current_user.user_id)
    if device is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="デバイスが見つからないか、所有者ではありません",
        )

    await update_device_setup(
        db,
        device,
        notification_target=body.notification_target,
        detection_targets=body.detection_targets,
    )

    return DeviceSetupResponse(device_id=device_id)
