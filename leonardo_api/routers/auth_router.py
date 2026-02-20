"""
routers/auth_router.py — 認証系エンドポイント

POST /api/v1/auth/register  - アカウント作成
POST /api/v1/auth/login     - ログイン → JWT 発行
POST /api/v1/auth/refresh   - アクセストークンのリフレッシュ
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from ..database import get_db
from ..models import User
from ..schemas import (
    AuthLoginRequest,
    AuthRegisterRequest,
    AuthRefreshRequest,
    TokenResponse,
)

router = APIRouter()


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="アカウント作成",
)
async def register(
    body: AuthRegisterRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TokenResponse:
    """
    メール + パスワードでアカウントを作成し、JWT を発行する。
    実証機では SMS OTP 認証は省略する。
    """
    # メール重複確認
    existing = await db.execute(select(User).where(User.email == body.email))
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="このメールアドレスはすでに登録されています",
        )

    user = User(
        email=body.email,
        password_hash=hash_password(body.password),
        phone_number=body.phone_number,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    return TokenResponse(
        access_token=create_access_token(user.user_id),
        refresh_token=create_refresh_token(user.user_id),
    )


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="ログイン",
)
async def login(
    body: AuthLoginRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TokenResponse:
    """メール + パスワードで認証し、JWT を発行する。"""
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()

    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="メールアドレスまたはパスワードが正しくありません",
        )

    return TokenResponse(
        access_token=create_access_token(user.user_id),
        refresh_token=create_refresh_token(user.user_id),
    )


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="アクセストークンのリフレッシュ",
)
async def refresh(
    body: AuthRefreshRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TokenResponse:
    """リフレッシュトークンを使って新しいアクセストークンを発行する。"""
    user_id = decode_token(body.refresh_token, expected_type="refresh")

    result = await db.execute(select(User).where(User.user_id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="ユーザーが見つかりません",
        )

    return TokenResponse(
        access_token=create_access_token(user.user_id),
        refresh_token=create_refresh_token(user.user_id),
    )
