"""models.py -- DB models (production-synced 2026-03-01)"""
import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Boolean, CheckConstraint, Column, DateTime,
    ForeignKey, Integer, Numeric, String, text,
)
from sqlalchemy.dialects.postgresql import INET, UUID
from sqlalchemy.orm import relationship
from .database import Base

class User(Base):
    __tablename__ = "users"
    user_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()"))
    email = Column(String(255), unique=True, nullable=False)
    phone_number = Column(String(20), nullable=True)
    password_hash = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    devices = relationship("Device", back_populates="owner")

class Device(Base):
    __tablename__ = "devices"
    device_id = Column(String(30), primary_key=True)
    cpu_serial = Column(String(20), nullable=True)
    owner_user_id = Column(UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=True)
    factory_token_hash = Column(String(64), nullable=False)
    api_token = Column(String(255), nullable=True)
    token_expires_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(20), nullable=False, server_default=text("'active'"))
    plan_type = Column(String(20), nullable=False, server_default=text("'consumer'"))
    notification_target = Column(String(500), nullable=True)
    detection_targets = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    last_seen = Column(DateTime(timezone=True), nullable=True)
    __table_args__ = (
        CheckConstraint("status IN ('active', 'suspended')", name="devices_status_check"),
        CheckConstraint("plan_type IN ('consumer', 'ultimate')", name="devices_plan_type_check"),
    )
    owner = relationship("User", back_populates="devices")
    events = relationship("DetectionEvent", back_populates="device")
    location_history = relationship("LocationHistory", back_populates="device")

class DetectionEvent(Base):
    __tablename__ = "detection_events"
    id = Column(Integer, primary_key=True, autoincrement=True)
    device_id = Column(String(30), ForeignKey("devices.device_id"), nullable=False)
    detected_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    detection_type = Column(String(20), nullable=True)
    confidence = Column(Numeric(5, 4), nullable=True)
    image_path = Column(String(500), nullable=True)
    image_url = Column(String(255), nullable=True)
    ip_address = Column(String(45), nullable=True)
    ip_geolocation_region = Column(String(100), nullable=True)
    distance_from_registered_km = Column(Numeric(10, 3), nullable=True)
    location_mismatch = Column(Boolean, nullable=False, server_default=text("false"))
    device = relationship("Device", back_populates="events")

class LocationHistory(Base):
    __tablename__ = "location_history"
    id = Column(Integer, primary_key=True, autoincrement=True)
    device_id = Column(String(30), ForeignKey("devices.device_id"), nullable=False)
    lat = Column(Numeric(10, 8), nullable=False)
    lon = Column(Numeric(11, 8), nullable=False)
    accuracy = Column(Numeric(8, 2), nullable=True)
    registered_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    registered_by = Column(UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=False)
    active_flag = Column(Boolean, nullable=False, server_default=text("true"))
    ip_address = Column(INET, nullable=True)
    user_agent = Column(String(255), nullable=True)
    device = relationship("Device", back_populates="location_history")
