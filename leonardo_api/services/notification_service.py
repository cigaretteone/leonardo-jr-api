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

        with smtplib.SMTP_SSL(settings.SMTP_HOST, 465) as server:
            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.send_message(msg)
        return True
    except smtplib.SMTPException as e:
        logger.error("メール送信エラー (to=%s): %s", to_email, e)
        return False



# ── Twilio phone call ───────────────────────────────────
import time as _time
_last_call_time = 0
CALL_COOLDOWN_SEC = 300

def _make_phone_call(to_phone, detection_type, confidence, device_id, latitude=None, longitude=None):
    global _last_call_time
    if _time.time() - _last_call_time < CALL_COOLDOWN_SEC:
        logger.info("Phone call skipped: cooldown (%ds left)", int(CALL_COOLDOWN_SEC - (_time.time() - _last_call_time)))
        return False
    if not settings.TWILIO_ACCOUNT_SID or not settings.TWILIO_AUTH_TOKEN:
        logger.warning("Twilio not configured")
        return False
    try:
        from twilio.rest import Client
        client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
        label = {"bear": "\u718a", "person": "\u4eba"}.get(detection_type, detection_type)
        conf_pct = int(confidence * 100)
        loc = ""
        if latitude and longitude:
            loc = f". GPS {latitude:.4f}, {longitude:.4f}"
        twiml = (
            '<Response><Say language="ja-JP" voice="alice">'
            f'Leonardo Jr \u691c\u77e5\u30a2\u30e9\u30fc\u30c8\u3002'
            f'\u30c7\u30d0\u30a4\u30b9 {device_id} \u3067 {label} \u3092\u691c\u77e5\u3057\u307e\u3057\u305f\u3002'
            f'\u4fe1\u983c\u5ea6 {conf_pct} \u30d1\u30fc\u30bb\u30f3\u30c8{loc}\u3002'
            f'\u7e70\u308a\u8fd4\u3057\u307e\u3059\u3002{label} \u3092\u691c\u77e5\u3002\u4fe1\u983c\u5ea6 {conf_pct} \u30d1\u30fc\u30bb\u30f3\u30c8\u3002'
            '</Say></Response>'
        )
        call = client.calls.create(twiml=twiml, to=to_phone, from_=settings.TWILIO_FROM_NUMBER)
        _last_call_time = _time.time()
        logger.info("Phone call initiated: to=%s sid=%s", to_phone, call.sid)
        return True
    except Exception as e:
        logger.error("Phone call failed: %s", e)
        return False

async def send_detection_notification(
    notification_target_json: str | None,
    device_id: str,
    detection_type: str,
    confidence: float,
    latitude: float | None = None,
    longitude: float | None = None,
    occurred_at=None,
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

    label = {"bear": "熊", "person": "人", "human": "人", "vehicle": "車両"}.get(detection_type, detection_type)
    # Format time in JST
    time_str = "不明"
    if occurred_at:
        try:
            from datetime import timezone, timedelta
            jst = timezone(timedelta(hours=9))
            jst_time = occurred_at.astimezone(jst)
            time_str = jst_time.strftime("%Y/%m/%d %H:%M:%S JST")
        except Exception:
            time_str = str(occurred_at)
    # GPS info
    gps_str = "不明"
    map_link = ""
    if latitude and longitude:
        gps_str = f"{latitude:.6f}, {longitude:.6f}"
        map_link = f"https://maps.google.com/maps?q={latitude},{longitude}"
    message = (
        f"\n【Leonardo Jr. 検知アラート】\n"
        f"デバイス: {device_id}\n"
        f"検知対象: {label}\n"
        f"信頼度: {confidence * 100:.1f}%\n"
        f"検知時刻: {time_str}\n"
        f"GPS座標: {gps_str}\n"
        + (f"地図: {map_link}\n" if map_link else "")
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


    # ── Phone call (bear only, 5min cooldown) ──
    if phone := target.get("phone"):
        if detection_type in ("bear",):
            import asyncio
            await asyncio.to_thread(
                _make_phone_call, phone, detection_type, confidence,
                device_id, latitude, longitude,
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
