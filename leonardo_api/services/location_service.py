"""
services/location_service.py — 位置登録ロジック

active_flag 制御は同一トランザクション内で実行する（設計書 §6.3）。
"""

import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import LocationHistory


_ACCURACY_WARN_M = 50.0    # 50m 超で警告
_ACCURACY_ALERT_M = 100.0  # 100m 超で追加警告（拒否はしない）


def _build_accuracy_warning(accuracy: float | None) -> str | None:
    """accuracy に応じた警告メッセージを返す（警告不要なら None）。"""
    if accuracy is None:
        return None
    if accuracy > _ACCURACY_ALERT_M:
        return f"GPS 精度が低い状態です（{accuracy:.0f}m）。より精度の高い場所で再取得することを推奨します。"
    if accuracy > _ACCURACY_WARN_M:
        return f"GPS 精度が {accuracy:.0f}m です。50m 以下での登録を推奨します。"
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
    """
    設置座標を登録する。

    トランザクション内で:
      1. 既存の active_flag = TRUE を全て FALSE に更新
      2. 新規レコードを active_flag = TRUE で挿入

    Returns:
        (新規 LocationHistory, 警告メッセージ or None)
    """
    warning = _build_accuracy_warning(accuracy)

    async with db.begin():
        # 既存のアクティブ座標を無効化（履歴として保持）
        await db.execute(
            update(LocationHistory)
            .where(
                LocationHistory.device_id == device_id,
                LocationHistory.active_flag.is_(True),
            )
            .values(active_flag=False)
        )

        # 新規座標を挿入
        new_location = LocationHistory(
            device_id=device_id,
            lat=lat,
            lon=lon,
            accuracy=accuracy,
            registered_by=user_id,
            active_flag=True,
            ip_address=ip_address,
        )
        db.add(new_location)

    await db.refresh(new_location)
    return new_location, warning


async def get_active_location(
    db: AsyncSession,
    device_id: str,
) -> LocationHistory | None:
    """デバイスの現在有効な設置座標を返す。"""
    result = await db.execute(
        select(LocationHistory)
        .where(
            LocationHistory.device_id == device_id,
            LocationHistory.active_flag.is_(True),
        )
        .order_by(LocationHistory.registered_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()
