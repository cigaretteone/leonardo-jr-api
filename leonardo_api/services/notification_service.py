"""
services/notification_service.py — 通知送信

実証機では LINE Notify と SMTP メールをサポートする。
既存の Leonardo Jr. ファームウェアの LINE Notify 連携を流用する方針に合わせ、
LINE Notify を第一優先とする。

通知タイミング:
  - 検知イベント発生時
  - 位置逸脱検知時（150km 超 or 都道府県不一致）
"""

import json
import logging
import smtplib
from email.mime.text import MIMEText

import httpx

from ..config import settings

logger = logging.getLogger(__name__)


def _parse_notification_target(notification_target_json: str | None) -> dict:
    """
    devices.notification_target（JSON 文字列）をパースして返す。
    パース失敗または None の場合は空辞書を返す。
    """
    if not notification_target_json:
        return {}
    try:
        return json.loads(notification_target_json)
    except (json.JSONDecodeError, TypeError):
        logger.warning("notification_target のパースに失敗: %s", notification_target_json)
        return {}


async def _send_line_notify(line_token: str, message: str) -> bool:
    """
    LINE Notify にメッセージを送信する。

    Args:
        line_token: LINE Notify のアクセストークン
        message:    送信メッセージ（最大 1000 文字）

    Returns:
        True: 送信成功、False: 失敗
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                settings.LINE_NOTIFY_API_URL,
                headers={"Authorization": f"Bearer {line_token}"},
                data={"message": message},
            )
        if resp.status_code == 200:
            return True
        logger.warning("LINE Notify 送信失敗 (status=%d): %s", resp.status_code, resp.text)
        return False
    except httpx.HTTPError as e:
        logger.error("LINE Notify 送信エラー: %s", e)
        return False


def _send_email_sync(to_email: str, subject: str, body: str) -> bool:
    """
    SMTP でメールを送信する（同期）。

    FastAPI の非同期コンテキストから呼ぶ場合は asyncio.to_thread() でラップすること。
    実証機規模では同期で十分（通知の遅延は許容範囲内）。
    """
    if not settings.SMTP_USER or not settings.SMTP_PASSWORD:
        logger.warning("SMTP 設定が未完了のためメール送信をスキップします")
        return False
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = settings.SMTP_FROM
        msg["To"] = to_email

        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
            server.starttls()
            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.send_message(msg)
        return True
    except smtplib.SMTPException as e:
        logger.error("メール送信エラー (to=%s): %s", to_email, e)
        return False


async def send_detection_notification(
    notification_target_json: str | None,
    device_id: str,
    detection_type: str,
    confidence: float,
) -> None:
    """
    検知イベントを所有者に通知する。

    notification_target に line_token があれば LINE Notify、
    email があればメールで送信する。
    """
    target = _parse_notification_target(notification_target_json)
    if not target:
        logger.debug("通知先が未設定のためスキップ (device_id=%s)", device_id)
        return

    label = {"bear": "熊", "human": "人", "vehicle": "車両"}.get(detection_type, detection_type)
    message = (
        f"\n【Leonardo Jr. 検知アラート】\n"
        f"デバイス: {device_id}\n"
        f"検知対象: {label}\n"
        f"信頼度: {confidence * 100:.1f}%"
    )

    if line_token := target.get("line_token"):
        await _send_line_notify(line_token, message)

    if email := target.get("email"):
        import asyncio
        await asyncio.to_thread(
            _send_email_sync,
            email,
            f"【Leonardo Jr.】{label}を検知しました",
            message,
        )


async def send_mismatch_alert(
    notification_target_json: str | None,
    device_id: str,
    distance_km: float | None,
    event_region: str,
) -> None:
    """
    位置逸脱を所有者に通知する。

    実証機では通知のみ。自動ロックはしない（設計書 §7.3）。
    """
    target = _parse_notification_target(notification_target_json)
    if not target:
        return

    dist_str = f"{distance_km:.0f}km" if distance_km is not None else "不明"
    message = (
        f"\n【Leonardo Jr. 位置逸脱アラート】\n"
        f"デバイス: {device_id}\n"
        f"発報地域: {event_region or '不明'}\n"
        f"登録座標との距離: {dist_str}\n"
        f"※ デバイスが設置場所から大きく離れた場所から通信しています。"
    )

    if line_token := target.get("line_token"):
        await _send_line_notify(line_token, message)

    if email := target.get("email"):
        import asyncio
        await asyncio.to_thread(
            _send_email_sync,
            email,
            "【Leonardo Jr.】位置逸脱を検知しました",
            message,
        )
