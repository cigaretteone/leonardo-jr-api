from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select, delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import Device, DeviceSubscriber
from ..schemas import (
    SetupAuthRequest,
    SetupAuthResponse,
    SubscriberCreate,
    SubscriberOut,
)
from ..services import pin_auth, setup_session, rate_limiter

router = APIRouter()


async def require_setup_session(
    device_id: str,
    request: Request,
) -> str:
    token = request.cookies.get(setup_session.COOKIE_NAME)
    if not token or not setup_session.verify(token, device_id):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="セッションが無効または期限切れです",
        )
    return device_id


@router.post(
    "/{device_id}/auth",
    response_model=SetupAuthResponse,
)
async def authenticate(
    device_id: str,
    body: SetupAuthRequest,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SetupAuthResponse:
    result = await db.execute(select(Device).where(Device.device_id == device_id))
    device = result.scalar_one_or_none()
    if device is None:
        raise HTTPException(status_code=404, detail="デバイスが見つかりません")

    locked, remaining = rate_limiter.is_locked(device_id)
    if locked:
        raise HTTPException(
            status_code=429,
            detail=f"ロック中です。{remaining}秒後に再試行してください",
        )

    if not pin_auth.verify_pin(device_id, body.pin):
        now_locked = rate_limiter.record_failure(device_id)
        if now_locked:
            raise HTTPException(status_code=429, detail="15分間ロックされました")
        raise HTTPException(status_code=401, detail="PIN が正しくありません")

    rate_limiter.reset(device_id)
    token = setup_session.issue(device_id)
    response.set_cookie(
        key=setup_session.COOKIE_NAME,
        value=token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=setup_session.SESSION_TTL_SECONDS,
        path=f"/api/v1/setup/{device_id}",
    )
    return SetupAuthResponse(ok=True)


@router.get(
    "/{device_id}/subscribers",
    response_model=list[SubscriberOut],
)
async def list_subscribers(
    device_id: Annotated[str, Depends(require_setup_session)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[SubscriberOut]:
    result = await db.execute(
        select(DeviceSubscriber)
        .where(DeviceSubscriber.device_id == device_id)
        .order_by(DeviceSubscriber.id.asc())
    )
    return [SubscriberOut.model_validate(s) for s in result.scalars().all()]


@router.post(
    "/{device_id}/subscribers",
    response_model=SubscriberOut,
    status_code=status.HTTP_201_CREATED,
)
async def add_subscriber(
    body: SubscriberCreate,
    device_id: Annotated[str, Depends(require_setup_session)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SubscriberOut:
    sub = DeviceSubscriber(
        device_id=device_id,
        channel=body.channel,
        target=body.target,
        label=body.label,
    )
    db.add(sub)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="同じ通知先がすでに登録されています")
    await db.refresh(sub)
    return SubscriberOut.model_validate(sub)


@router.delete(
    "/{device_id}/subscribers/{sub_id}",
)
async def delete_subscriber(
    sub_id: int,
    device_id: Annotated[str, Depends(require_setup_session)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    result = await db.execute(
        delete(DeviceSubscriber)
        .where(DeviceSubscriber.id == sub_id)
        .where(DeviceSubscriber.device_id == device_id)
    )
    await db.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="通知先が見つかりません")
    return {"ok": True}


@router.post(
    "/{device_id}/test",
)
async def test_notification(
    device_id: Annotated[str, Depends(require_setup_session)],
) -> dict:
    raise HTTPException(status_code=501, detail="Phase 21 で実装予定")


@router.post(
    "/{device_id}/reset",
)
async def reset_subscribers(
    device_id: Annotated[str, Depends(require_setup_session)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    await db.execute(
        delete(DeviceSubscriber).where(DeviceSubscriber.device_id == device_id)
    )
    await db.commit()
    return {"ok": True}
