"""
models.py — SQLAlchemy ORM モデル

DB テーブルと 1:1 で対応する。Phase 2 のスキーマ（001_initial_schema.sql）と整合させる。
SQLAlchemy 2.0 の Mapped / mapped_column スタイルで定義する。
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, String, text
from sqlalchemy.dialects.postgresql import INET, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    phone_number: Mapped[str | None] = mapped_column(String(20))
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP")
    )

    # リレーション
    devices: Mapped[list["Device"]] = relationship(back_populates="owner")


class Device(Base):
    __tablename__ = "devices"

    device_id: Mapped[str] = mapped_column(String(30), primary_key=True)
    cpu_serial: Mapped[str | None] = mapped_column(String(20))
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id")
    )
    factory_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    api_token: Mapped[str | None] = mapped_column(String(255))
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), server_default="active")
    plan_type: Mapped[str] = mapped_column(String(20), server_default="consumer")
    # JSON 文字列で保持（例: '{"line_token":"xxx","email":"a@b.com"}'）
    notification_target: Mapped[str | None] = mapped_column(String(500))
    # JSON 配列文字列（例: '["bear","human"]'）
    detection_targets: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP")
    )

    # リレーション
    owner: Mapped["User | None"] = relationship(back_populates="devices")
    location_history: Mapped[list["LocationHistory"]] = relationship(
        back_populates="device"
    )
    detection_events: Mapped[list["DetectionEvent"]] = relationship(
        back_populates="device"
    )


class LocationHistory(Base):
    __tablename__ = "location_history"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(
        String(30), ForeignKey("devices.device_id"), nullable=False
    )
    lat: Mapped[float] = mapped_column(Numeric(10, 8), nullable=False)
    lon: Mapped[float] = mapped_column(Numeric(11, 8), nullable=False)
    accuracy: Mapped[float | None] = mapped_column(Numeric(8, 2))
    registered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP")
    )
    registered_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=False
    )
    active_flag: Mapped[bool] = mapped_column(Boolean, default=True)
    ip_address: Mapped[str | None] = mapped_column(INET)

    # リレーション
    device: Mapped["Device"] = relationship(back_populates="location_history")


class DetectionEvent(Base):
    __tablename__ = "detection_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(
        String(30), ForeignKey("devices.device_id"), nullable=False
    )
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP")
    )
    detection_type: Mapped[str | None] = mapped_column(String(20))
    confidence: Mapped[float | None] = mapped_column(Numeric(5, 4))
    image_path: Mapped[str | None] = mapped_column(String(500))
    ip_address: Mapped[str | None] = mapped_column(INET)
    ip_geolocation_region: Mapped[str | None] = mapped_column(String(100))
    distance_from_registered_km: Mapped[float | None] = mapped_column(Numeric(10, 3))
    location_mismatch: Mapped[bool] = mapped_column(Boolean, default=False)

    # リレーション
    device: Mapped["Device"] = relationship(back_populates="detection_events")
