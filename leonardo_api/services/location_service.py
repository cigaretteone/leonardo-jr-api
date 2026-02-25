"""
services/location_service.py
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import LocationHistory


_ACCURACY_WARN_M = 50.0
_ACCURACY_ALERT_M = 100.0


def _build_accuracy_warning(accuracy: float | None) -> str | None:
    if accuracy is None:
        return None
    if accuracy > _ACCURACY_ALERT_M:
        return f"GPS precision low ({accuracy:.0f}m). Re-register recommended."
    if accuracy > _ACCURACY_WARN_M:
        return f"GPS precision {accuracy:.0f}m. Under 50m recommended."
    return None


async def register_location(
    db: AsyncSession,
    device_id: str,
    lat: float,
    lon: float,
    accuracy: float | None,
    user_id: uuid.UUID,
    ip_address: str | None,
) -> tuple[LocationHistory, str | None]:
    warning = _build_accuracy_warning(accuracy)
    new_location = LocationHistory(
        device_id=device_id,
        lat=lat,
        lon=lon,
        accuracy=accuracy,
        ip_address=ip_address,
    )
    db.add(new_location)
    await db.commit()
    await db.refresh(new_location)
    return new_location, warning


async def get_active_location(
    db: AsyncSession,
    device_id: str,
) -> LocationHistory | None:
    result = await db.execute(
        select(LocationHistory)
        .where(LocationHistory.device_id == device_id)
        .order_by(LocationHistory.registered_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()