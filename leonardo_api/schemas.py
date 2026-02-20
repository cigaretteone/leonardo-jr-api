"""
schemas.py — Pydantic v2 リクエスト / レスポンススキーマ

ルーターで使う入出力型を定義する。
ORM モデル（models.py）との変換は各サービス層で行う。
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator


# =============================================================================
# 認証系
# =============================================================================

class AuthRegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, description="8文字以上")
    phone_number: str | None = Field(default=None, pattern=r"^\+?[\d\-]{7,20}$")


class AuthLoginRequest(BaseModel):
    email: EmailStr
    password: str


class AuthRefreshRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


# =============================================================================
# デバイス登録系
# =============================================================================

class DeviceRegisterResponse(BaseModel):
    device_id: str
    api_token: str
    message: str = "登録完了"


class DeviceSetupRequest(BaseModel):
    """
    通知先と検知対象を設定する。
    notification_target / detection_targets は JSON 文字列として DB に保存する。
    """
    notification_target: dict | None = Field(
        default=None,
        description='例: {"line_token": "xxx", "email": "a@b.com"}',
    )
    detection_targets: list[str] | None = Field(
        default=None,
        description='例: ["bear", "human", "vehicle"]',
    )

    @field_validator("detection_targets")
    @classmethod
    def validate_detection_targets(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        allowed = {"bear", "human", "vehicle", "unknown"}
        invalid = set(v) - allowed
        if invalid:
            raise ValueError(f"不明な検知対象: {invalid}。許可値: {allowed}")
        return v


class DeviceSetupResponse(BaseModel):
    device_id: str
    message: str = "セットアップ完了"


# =============================================================================
# 位置登録系
# =============================================================================

class LocationRegisterRequest(BaseModel):
    lat: float = Field(ge=-90.0, le=90.0, description="緯度")
    lon: float = Field(ge=-180.0, le=180.0, description="経度")
    accuracy: float | None = Field(default=None, ge=0.0, description="GPS 精度（メートル）")


class LocationRegisterResponse(BaseModel):
    location_id: int
    warning: str | None = None  # accuracy が 50m 超の場合に設定


class RelocateRequest(BaseModel):
    """再設置リクエスト。所有者のパスワード再入力が必須。"""
    password: str
    lat: float = Field(ge=-90.0, le=90.0)
    lon: float = Field(ge=-180.0, le=180.0)
    accuracy: float | None = Field(default=None, ge=0.0)


class RelocateResponse(BaseModel):
    location_id: int
    warning: str | None = None


# =============================================================================
# 検知イベント系
# =============================================================================

class DetectionEventRequest(BaseModel):
    """
    デバイスから送信される検知イベント。
    APIトークン認証で送信される（JWT ではなく api_token）。
    """
    detection_type: str = Field(
        description="bear / human / vehicle / unknown"
    )
    confidence: float = Field(ge=0.0, le=1.0, description="AI 信頼度スコア")
    image_base64: str | None = Field(
        default=None, description="検知画像の Base64 文字列（省略可）"
    )
    timestamp: datetime | None = Field(
        default=None,
        description="検知日時（UTC）。None の場合はサーバ受信時刻を使用。",
    )


class DetectionEventResponse(BaseModel):
    event_id: int
    location_mismatch: bool


class OfflineEventItem(BaseModel):
    """圏外中に溜まったイベントの 1 件分。"""
    detection_type: str
    confidence: float = Field(ge=0.0, le=1.0)
    timestamp: datetime
    image_base64: str | None = None


class UploadLogsRequest(BaseModel):
    """圏外中に溜まった JSONL ログの一括アップロード。"""
    events: list[OfflineEventItem] = Field(min_length=1)


class UploadLogsResponse(BaseModel):
    inserted: int
    message: str = "ログアップロード完了"


# =============================================================================
# デバイスステータス
# =============================================================================

class ActiveLocation(BaseModel):
    lat: float
    lon: float
    accuracy: float | None
    registered_at: datetime


class DeviceStatusResponse(BaseModel):
    status: str
    active_location: ActiveLocation | None
