"""
services/device_service.py — デバイス登録ロジック

factory_token 検証・devices レコード作成・api_token 発行を担う。
"""

import hashlib
import json
import secrets
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import Device


def _derive_factory_token(device_id: str) -> str:
    """
    device_id と固定シークレットから factory_token を導出する。
    デバイス側（generate_qr.py）と同じ計算式で再導出し、照合に使う。
    """
    raw = f"{device_id}:{settings.FACTORY_SECRET}".encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def _derive_factory_token_hash(factory_token: str) -> str:
    """factory_token をハッシュ化して factory_token_hash を返す。"""
    return hashlib.sha256(factory_token.encode()).hexdigest()[:16]


def verify_factory_token_hash(device_id: str, fth: str) -> bool:
    """
    QR の fth パラメータを検証する。

    サーバー側で factory_token を再導出し、そのハッシュと fth を比較する。
    タイミング攻撃対策に secrets.compare_digest を使用する。
    """
    expected = _derive_factory_token_hash(_derive_factory_token(device_id))
    return secrets.compare_digest(expected, fth)


def _compute_factory_token_hash(device_id: str) -> str:
    """devices テーブルに保存する factory_token_hash を返す。"""
    return _derive_factory_token_hash(_derive_factory_token(device_id))


def _generate_api_token() -> str:
    """デバイス通信用の api_token を生成する（URL-safe 32バイト）。"""
    return secrets.token_urlsafe(32)


async def register_device(
    db: AsyncSession,
    device_id: str,
    owner_user_id: uuid.UUID,
) -> Device:
    """
    デバイスを所有者に紐付ける。

    - devices レコードが存在しない -> その場で作成（pre-register なし）
    - 存在 + owner_user_id IS NULL -> 登録可能
    - 存在 + owner 設定済み -> ValueError を送出（呼び出し元で 409 を返すこと）

    Returns:
        登録済みの Device オブジェクト（api_token が設定されている）

    Raises:
        ValueError: デバイスに既に owner_user_id が設定されている場合
        IntegrityError: DB 制約違反（同時登録競合等）が発生した場合
    """
    result = await db.execute(select(Device).where(Device.device_id == device_id))
    device = result.scalar_one_or_none()

    # サービス層の二重チェック: ルーター側チェック後に競合が生じた場合のセーフガード
    if device is not None and device.owner_user_id is not None:
        raise ValueError(
            f"デバイス {device_id} はすでに登録済みです（owner_user_id IS NOT NULL）。"
        )

    api_token = _generate_api_token()
    fth = _compute_factory_token_hash(device_id)

    try:
        if device is None:
            # devices レコードをこの時点で新規作成
            device = Device(
                device_id=device_id,
                factory_token_hash=fth,
                owner_user_id=owner_user_id,
                api_token=api_token,
                status="active",
                plan_type="consumer",
            )
            db.add(device)
        else:
            # 既存レコードに owner と api_token を設定
            device.owner_user_id = owner_user_id
            device.api_token = api_token

        await db.commit()
        await db.refresh(device)
    except IntegrityError:
        await db.rollback()
        raise

    return device


async def get_device_for_owner(
    db: AsyncSession,
    device_id: str,
    user_id: uuid.UUID,
) -> Device | None:
    """
    device_id が指定ユーザーの所有物であれば返す。
    所有者でない場合は None を返す。
    """
    result = await db.execute(
        select(Device).where(
            Device.device_id == device_id,
            Device.owner_user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


async def update_device_setup(
    db: AsyncSession,
    device: Device,
    notification_target: dict | None,
    detection_targets: list[str] | None,
) -> Device:
    """通知先や検知対象を devices テーブルに保存する。"""
    if notification_target is not None:
        device.notification_target = json.dumps(notification_target, ensure_ascii=False)
    if detection_targets is not None:
        device.detection_targets = json.dumps(detection_targets, ensure_ascii=False)

    await db.commit()
    await db.refresh(device)
    return device


async def update_last_seen(db: AsyncSession, device_id: str) -> None:
    """デバイスの最終通信時刻 (last_seen) を現在時刻で更新する（Heartbeat）。"""
    await db.execute(
        update(Device)
        .where(Device.device_id == device_id)
        .values(last_seen=datetime.now(timezone.utc))
    )
    # ここでは commit しない（呼び出し元で他の処理と一緒に commit されることを期待、または flush）
    # ただし、単独で呼ばれるケースもあるため、明示的に commit しておくのが安全
    await db.commit()