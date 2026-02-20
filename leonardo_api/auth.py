"""
auth.py — JWT 認証 / パスワードハッシュ / 依存注入

ユーザー向け JWT（アクセス+リフレッシュ）と
デバイス向け api_token（DB 照合）の2系統を管理する。
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .database import get_db
from .models import Device, User

# bcrypt コンテキスト（deprecated=auto でレガシーハッシュ自動アップグレード）
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Bearer トークンスキーム（JWT 用）
bearer_scheme = HTTPBearer()


# ---------------------------------------------------------------------------
# パスワードユーティリティ
# ---------------------------------------------------------------------------

def hash_password(plain: str) -> str:
    """パスワードを bcrypt でハッシュ化する。"""
    return pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """入力パスワードとハッシュを照合する。"""
    return pwd_context.verify(plain, hashed)


# ---------------------------------------------------------------------------
# JWT ユーティリティ
# ---------------------------------------------------------------------------

def _create_token(data: dict, expires_delta: timedelta) -> str:
    payload = data.copy()
    payload["exp"] = datetime.now(timezone.utc) + expires_delta
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_access_token(user_id: uuid.UUID) -> str:
    return _create_token(
        {"sub": str(user_id), "type": "access"},
        timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES),
    )


def create_refresh_token(user_id: uuid.UUID) -> str:
    return _create_token(
        {"sub": str(user_id), "type": "refresh"},
        timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS),
    )


def decode_token(token: str, expected_type: str = "access") -> uuid.UUID:
    """
    JWT を検証し、user_id（UUID）を返す。
    不正・期限切れの場合は 401 を送出する。
    """
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="認証情報が無効です",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(
            token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM]
        )
        if payload.get("type") != expected_type:
            raise credentials_error
        sub = payload.get("sub")
        if sub is None:
            raise credentials_error
        return uuid.UUID(sub)
    except (JWTError, ValueError):
        raise credentials_error


# ---------------------------------------------------------------------------
# FastAPI 依存注入
# ---------------------------------------------------------------------------

async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer_scheme)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """
    JWT アクセストークンを検証し、対応する User を返す。
    ルーターの Depends() で使用する。
    """
    user_id = decode_token(credentials.credentials, expected_type="access")

    result = await db.execute(select(User).where(User.user_id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="ユーザーが見つかりません",
        )
    return user


async def get_device_by_api_token(
    x_api_token: Annotated[str | None, Header()] = None,
    db: AsyncSession = Depends(get_db),
) -> Device:
    """
    X-Api-Token ヘッダーからデバイスを特定する。
    デバイス → サーバ通信（発報・ステータス確認）用。
    """
    if x_api_token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-Api-Token ヘッダーが必要です",
        )
    result = await db.execute(
        select(Device).where(Device.api_token == x_api_token)
    )
    device = result.scalar_one_or_none()
    if device is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="デバイストークンが無効です",
        )
    return device
