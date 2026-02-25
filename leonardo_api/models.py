"""
models.py — データベースモデル定義

Users, Devices, DetectionEvents, LocationHistory
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from .database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default="gen_random_uuid()",
    )
    email = Column(String(255), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    created_at = Column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    devices = relationship("Device", back_populates="owner")


class Device(Base):
    __tablename__ = "devices"

    device_id = Column(String(50), primary_key=True)
    factory_token_hash = Column(String(64), nullable=False)
    owner_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    api_token = Column(String(64), unique=True, nullable=True)
    
    # 追加: 最終通信時刻 (Heartbeat)
    last_seen = Column(DateTime(timezone=True), nullable=True)

    status = Column(String(20), default="active")  # active, suspended
    plan_type = Column(String(20), default="consumer")  # consumer, enterprise
    
    # JSON文字列として保存（簡易実装）
    notification_target = Column(Text, nullable=True)  # {"line_token": "..."}
    detection_targets = Column(Text, nullable=True)    # ["bear", "human"]

    created_at = Column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    owner = relationship("User", back_populates="devices")
    events = relationship("DetectionEvent", back_populates="device")
    location_history = relationship("LocationHistory", back_populates="device")


class DetectionEvent(Base):
    __tablename__ = "detection_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    device_id = Column(String(50), ForeignKey("devices.device_id"), nullable=False)
    
    detected_at = Column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    detection_type = Column(String(50), nullable=False)  # bear, human, vehicle
    confidence = Column(Float, nullable=False)
    
    image_url = Column(String(255), nullable=True)  # S3 URL etc (Future use)
    
    # ジオロケーション・逸脱検知用
    ip_address = Column(String(45), nullable=True)
    ip_geolocation_region = Column(String(100), nullable=True)  # "Tokyo", "Hokkaido"
    distance_from_registered_km = Column(Float, nullable=True)
    location_mismatch = Column(Boolean, default=False)

    device = relationship("Device", back_populates="events")


class LocationHistory(Base):
    __tablename__ = "location_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    device_id = Column(String(50), ForeignKey("devices.device_id"), nullable=False)
    
    registered_at = Column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    
    lat = Column(Float, nullable=False)
    lon = Column(Float, nullable=False)
    accuracy = Column(Float, nullable=True)
    
    ip_address = Column(String(45), nullable=True)
    user_agent = Column(String(255), nullable=True)
    active_flag = Column(Boolean, default=False)

    device = relationship("Device", back_populates="location_history")
