"""
routers/location_router.py — 位置登録・再設置エンドポイント

POST /api/v1/devices/{device_id}/location  - 初回・更新の設置座標登録
POST /api/v1/devices/{device_id}/relocate  - 再設置（パスワード再入力必須）
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user, verify_password
from ..database import get_db
from ..models import User
from ..schemas import (
    LocationRegisterRequest,
    LocationRegisterResponse,
    RelocateRequest,
    RelocateResponse,
)
from ..services.device_service import get_device_for_owner
from ..services.location_service import register_location

router = APIRouter()


def _get_client_ip(request: Request) -> str | None:
    """リクエスト元 IP を取得する（リバースプロキシ越しも考慮）。"""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


@router.post(
    "/{device_id}/location",
    response_model=LocationRegisterResponse,
    status_code=status.HTTP_201_CREATED,
    summary="設置座標登録",
)
async def register_location_endpoint(
    device_id: str,
    body: LocationRegisterRequest,
    request: Request,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> LocationRegisterResponse:
    """
    スマホ GPS で取得した座標を登録する。

    accuracy が 50m 超の場合は warning を返す（拒否はしない）。
    active_flag の切り替えは同一トランザクション内で実行する。
    """
    device = await get_device_for_owner(db, device_id, current_user.user_id)
    if device is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="デバイスが見つからないか、所有者ではありません",
        )

    ip = _get_client_ip(request)
    location, warning = await register_location(
        db,
        device_id=device_id,
        lat=body.lat,
        lon=body.lon,
        accuracy=body.accuracy,
        user_id=current_user.user_id,
        ip_address=ip,
    )

    return LocationRegisterResponse(location_id=location.id, warning=warning)


@router.post(
    "/{device_id}/relocate",
    response_model=RelocateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="再設置（ゲリラ展開）",
)
async def relocate_endpoint(
    device_id: str,
    body: RelocateRequest,
    request: Request,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> RelocateResponse:
    """
    デバイスを新しい場所に再設置する。所有者のパスワード再入力が必須。

    旧座標は location_history に履歴として残る（上書きしない）。
    """
    # 所有者確認
    device = await get_device_for_owner(db, device_id, current_user.user_id)
    if device is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="デバイスが見つからないか、所有者ではありません",
        )

    # パスワード再入力確認（再設置の意図的操作を保証する）
    if not verify_password(body.password, current_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="パスワードが正しくありません",
        )

    ip = _get_client_ip(request)
    location, warning = await register_location(
        db,
        device_id=device_id,
        lat=body.lat,
        lon=body.lon,
        accuracy=body.accuracy,
        user_id=current_user.user_id,
        ip_address=ip,
    )

    return RelocateResponse(location_id=location.id, warning=warning)
