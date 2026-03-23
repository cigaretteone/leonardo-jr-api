"""
schemas.py — Pydantic v2 リクエスト / レスポンススキーマ
Phase 1.1 update: UUID v7 event_id, device_id in request, ACK response
"""
import uuid
from uuid import UUID
from datetime import datetime
from typing import Any, Optional

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
    notification_target: dict | None = Field(
        default=None,
        description='例 {"line_token": "xxx", "email": "a@b.com"}',
    )
    detection_targets: list[str] | None = Field(
        default=None,
        description='例 ["bear", "human", "vehicle"]',
    )

    @field_validator("detection_targets")
    @classmethod
    def validate_detection_targets(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        allowed = {"bear", "human", "vehicle", "unknown"}
        invalid = set(v) - allowed
        if invalid:
            raise ValueError(f"不正な検知対象: {invalid}。許可値: {allowed}")
        return v


class DeviceSetupResponse(BaseModel):
    device_id: str
    message: str = "セットアップ完了"


# =============================================================================
# 座標登録系
# =============================================================================

class LocationRegisterRequest(BaseModel):
    lat: float = Field(ge=-90.0, le=90.0, description="緯度")
    lon: float = Field(ge=-180.0, le=180.0, description="経度")
    accuracy: float | None = Field(default=None, ge=0.0, description="GPS精度（メートル）")


class LocationRegisterResponse(BaseModel):
    location_id: int
    warning: str | None = None


class RelocateRequest(BaseModel):
    password: str
    lat: float = Field(ge=-90.0, le=90.0)
    lon: float = Field(ge=-180.0, le=180.0)
    accuracy: float | None = Field(default=None, ge=0.0)


class RelocateResponse(BaseModel):
    location_id: int
    warning: str | None = None


# =============================================================================
# 検知イベント系 (Phase 1.1: UUID v7 冪等性対応)
# =============================================================================

class DetectionPayload(BaseModel):
    """検知の詳細。payload_json内にも保存される。"""
    class_name: str = Field(alias="class", description="bear / human / vehicle / unknown")
    confidence: float = Field(ge=0.0, le=1.0)
    distance_estimate: str | None = Field(default=None, description="near / mid / far")

    model_config = {"populate_by_name": True}


class GpsData(BaseModel):
    lat: float = Field(ge=-90.0, le=90.0)
    lon: float = Field(ge=-180.0, le=180.0)


class DeviceStatus(BaseModel):
    firmware_version: str | None = None
    battery_voltage: float | None = None
    signal_strength: int | None = None


class DetectionEventRequest(BaseModel):
    """
    Phase 1.1 統一イベントスキーマ。
    デバイスが UUID v7 を生成して event_id に設定する。
    サーバは ON CONFLICT (event_id) DO NOTHING で冪等性を保証。
    """
    event_id: uuid.UUID = Field(
        description="UUID v7 (RFC 9562) — デバイス側で生成",
    )
    device_id: str = Field(
        max_length=30,
        description="デバイスID（api_tokenと一致すること）",
    )
    event_type: str = Field(
        default="detection",
        max_length=32,
        description="detection / heartbeat / alert",
    )
    occurred_at: datetime = Field(
        description="検知時刻 (UTC)",
    )

    # --- 検知詳細 ---
    detection: DetectionPayload | None = None
    gps: GpsData | None = None
    device_status: DeviceStatus | None = None

    # --- 後方互換フィールド（旧lte_sender対応、移行期間中のみ） ---
    detection_type: str | None = Field(default=None, description="後方互換: bear/human等")
    confidence: float | None = Field(default=None, ge=0.0, le=1.0, description="後方互換")
    image_base64: str | None = Field(default=None, description="検知画像 Base64（省略可）")
    thumbnail_b64: str | None = Field(default=None, max_length=200000, description="Phase 2.1: JPEG thumbnail base64")
    timestamp: datetime | None = Field(default=None, description="後方互換: occurred_atを使用")

    def get_detection_type(self) -> str | None:
        """detection.class_name or 後方互換 detection_type を返す"""
        if self.detection:
            return self.detection.class_name
        return self.detection_type

    def get_confidence(self) -> float | None:
        """detection.confidence or 後方互換 confidence を返す"""
        if self.detection:
            return self.detection.confidence
        return self.confidence

    def get_occurred_at(self) -> datetime:
        """occurred_at を優先、なければ timestamp、なければ現在時刻"""
        return self.occurred_at


class DetectionEventResponse(BaseModel):
    """
    Phase 1.1 ACKレスポンス。
    status: "accepted" (201) or "duplicate" (200)
    """
    event_id: str = Field(description="受理されたイベントのUUID")
    status: str = Field(description="accepted / duplicate")
    location_mismatch: bool = False


# =============================================================================
# オフラインログ系（既存、変更なし）
# =============================================================================

class OfflineEventItem(BaseModel):
    detection_type: str
    confidence: float = Field(ge=0.0, le=1.0)
    timestamp: datetime
    image_base64: str | None = None


class UploadLogsRequest(BaseModel):
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

class EventResponseV2(BaseModel):
    status: str
    event_id: UUID
    video_requested: bool = False
    upload_url: Optional[str] = None
    class Config:
        from_attributes = True

class VideoUploadResponse(BaseModel):
    status: str
    media_id: Optional[UUID] = None
    class Config:
        from_attributes = True

class VideoUploadError(BaseModel):
    error: str
    detail: Optional[str] = None
    max_bytes: Optional[int] = None

class MediaInfo(BaseModel):
    media_id: UUID
    event_id: UUID
    media_type: str
    upload_status: str
    codec: Optional[str] = None
    resolution: Optional[str] = None
    duration_sec: Optional[float] = None
    file_size_bytes: Optional[int] = None
    sha256_hash: Optional[str] = None
    uploaded_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    class Config:
        from_attributes = True
