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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import DeviceSubscriber

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


async def _get_subscribers(db: AsyncSession, device_id: str) -> list[DeviceSubscriber]:
    """device_subscribers テーブルから enabled な行を取得する。"""
    result = await db.execute(
        select(DeviceSubscriber)
        .where(DeviceSubscriber.device_id == device_id)
        .where(DeviceSubscriber.enabled == True)
    )
    return list(result.scalars().all())


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




async def _send_line_message(user_id: str, message: str) -> bool:
    """LINE Messaging API Push Message."""
    token = settings.LINE_CHANNEL_ACCESS_TOKEN
    if not token:
        logger.warning("LINE_CHANNEL_ACCESS_TOKEN not set")
        return False
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "https://api.line.me/v2/bot/message/push",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json={
                    "to": user_id,
                    "messages": [{"type": "text", "text": message}],
                },
            )
        if resp.status_code == 200:
            logger.info("LINE message sent to %s", user_id[:10])
            return True
        logger.warning("LINE send failed (status=%d): %s", resp.status_code, resp.text)
        return False
    except Exception as e:
        logger.error("LINE send error: %s", e)
        return False

# ── Twilio phone call ───────────────────────────────────
import time as _time
_last_call_time = 0
CALL_COOLDOWN_SEC = 300
EMAIL_COOLDOWN_SEC = 120
_last_email_times = {}

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
            f'Leonardo Jr {label}検知アラート。'
            f'信頼度 {conf_pct} パーセント。'
            f'繰り返します。{label}を検知しました。'
            f'必要に応じてメールやアプリでご確認ください。'
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
    db: AsyncSession | None = None,
) -> None:
    """
    検知イベントを通知する。

    Phase 21: subscribers 優先 + notification_target フォールバック。
    db が渡され subscribers に enabled な行があればそちらを使い、
    なければ既存 notification_target JSON 経由で通知する。
    """
    # メッセージ整形 (共通)
    label = {"bear": "熊", "person": "人", "human": "人", "vehicle": "車両"}.get(detection_type, detection_type)
    time_str = "不明"
    if occurred_at:
        try:
            from datetime import timezone, timedelta
            jst = timezone(timedelta(hours=9))
            jst_time = occurred_at.astimezone(jst)
            time_str = jst_time.strftime("%Y/%m/%d %H:%M:%S JST")
        except Exception:
            time_str = str(occurred_at)
    gps_str = "不明"
    map_link = ""
    if latitude and longitude:
        gps_str = f"{latitude:.6f}, {longitude:.6f}"
        map_link = f"https://maps.google.com/maps?q={latitude},{longitude}"

    full_message = (
        f"\n【Leonardo Jr. 検知アラート】\n"
        f"デバイス: {device_id}\n"
        f"検知対象: {label}\n"
        f"信頼度: {confidence * 100:.1f}%\n"
        f"検知時刻: {time_str}\n"
        f"GPS座標: {gps_str}\n"
        + (f"地図: {map_link}\n" if map_link else "")
    )
    line_short = (
        f"\u3010Leonardo Jr.\u3011\n"
        f"{label}\u3092\u691c\u77e5\u3057\u307e\u3057\u305f\n"
        f"\u4fe1\u983c\u5ea6: {confidence * 100:.0f}%\n"
        f"\u6642\u523b: {time_str}\n"
        + (f"\u5730\u56f3: {map_link}\n" if map_link else "")
        + f"\n\u25b6 \u78ba\u8a8d: https://leonardo-jr-api.onrender.com/events"
    )

    # subscribers 優先
    subscribers = []
    if db is not None:
        try:
            subscribers = await _get_subscribers(db, device_id)
        except Exception as e:
            logger.warning("subscribers の取得に失敗、フォールバックします: %s", e)
            subscribers = []

    if subscribers:
        await _dispatch_to_subscribers(
            subscribers=subscribers,
            device_id=device_id,
            label=label,
            detection_type=detection_type,
            confidence=confidence,
            latitude=latitude,
            longitude=longitude,
            full_message=full_message,
            line_short=line_short,
            email_subject=f"【Leonardo Jr.】{label}を検知しました",
        )
        return

    # フォールバック: 既存 notification_target 経由
    target = _parse_notification_target(notification_target_json)
    if not target:
        logger.debug("通知先が未設定のためスキップ (device_id=%s)", device_id)
        return

    if line_token := target.get("line_token"):
        await _send_line_notify(line_token, full_message)

    email = target.get("email")
    if email and target.get("email_enabled", True) is False:
        email = None
    if email:
        _em_now = _time.time()
        _em_last = _last_email_times.get(device_id, 0)
        if _em_now - _em_last >= EMAIL_COOLDOWN_SEC:
            _last_email_times[device_id] = _em_now
            import asyncio
            await asyncio.to_thread(
                _send_email_sync,
                email,
                f"【Leonardo Jr.】{label}を検知しました",
                full_message,
            )
        else:
            logger.info("Email cooldown active (%ds left)", int(EMAIL_COOLDOWN_SEC - (_em_now - _em_last)))

    line_uid = target.get("line_user_id")
    if line_uid and target.get("line_enabled", True) is False:
        line_uid = None
    if line_uid:
        await _send_line_message(line_uid, line_short)

    phone = target.get("phone")
    if phone and target.get("call_enabled", True) is False:
        phone = None
    if phone and detection_type in ("bear",):
        import asyncio
        await asyncio.to_thread(
            _make_phone_call, phone, detection_type, confidence,
            device_id, latitude, longitude,
        )


async def _dispatch_to_subscribers(
    subscribers: list[DeviceSubscriber],
    device_id: str,
    label: str,
    detection_type: str,
    confidence: float,
    latitude: float | None,
    longitude: float | None,
    full_message: str,
    line_short: str,
    email_subject: str,
) -> None:
    """subscribers 経由で channel 別に通知配信する。"""
    import asyncio

    line_subs = [s for s in subscribers if s.channel == "line"]
    email_subs = [s for s in subscribers if s.channel == "email"]
    phone_subs = [s for s in subscribers if s.channel == "phone"]

    # LINE: 即時送信、cooldown なし
    for s in line_subs:
        await _send_line_message(s.target, line_short)

    # Email: device 単位で 120秒 cooldown を共有
    if email_subs:
        _em_now = _time.time()
        _em_last = _last_email_times.get(device_id, 0)
        if _em_now - _em_last >= EMAIL_COOLDOWN_SEC:
            _last_email_times[device_id] = _em_now
            for s in email_subs:
                await asyncio.to_thread(
                    _send_email_sync, s.target, email_subject, full_message,
                )
        else:
            logger.info(
                "Email cooldown active (%ds left), %d subscribers skipped",
                int(EMAIL_COOLDOWN_SEC - (_em_now - _em_last)),
                len(email_subs),
            )

    # Phone: bear のみ、device 単位で 300秒 cooldown を共有
    if phone_subs and detection_type == "bear":
        for s in phone_subs:
            await asyncio.to_thread(
                _make_phone_call, s.target, detection_type, confidence,
                device_id, latitude, longitude,
            )


async def send_mismatch_alert(
    notification_target_json: str | None,
    device_id: str,
    distance_km: float | None,
    event_region: str,
    db: AsyncSession | None = None,
) -> None:
    """位置逸脱を通知する。subscribers 優先 + notification_target フォールバック。"""
    dist_str = f"{distance_km:.0f}km" if distance_km is not None else "不明"
    full_message = (
        f"\n【Leonardo Jr. 位置逸脱アラート】\n"
        f"デバイス: {device_id}\n"
        f"発報地域: {event_region or '不明'}\n"
        f"登録座標との距離: {dist_str}\n"
        f"※ デバイスが設置場所から大きく離れた場所から通信しています。"
    )
    line_short = full_message
    email_subject = "【Leonardo Jr.】位置逸脱を検知しました"

    subscribers = []
    if db is not None:
        try:
            subscribers = await _get_subscribers(db, device_id)
        except Exception as e:
            logger.warning("subscribers の取得に失敗、フォールバックします: %s", e)
            subscribers = []

    if subscribers:
        # 位置逸脱は phone 通知しない (bear ではないので)
        await _dispatch_to_subscribers(
            subscribers=subscribers,
            device_id=device_id,
            label="",
            detection_type="mismatch",
            confidence=1.0,
            latitude=None,
            longitude=None,
            full_message=full_message,
            line_short=line_short,
            email_subject=email_subject,
        )
        return

    target = _parse_notification_target(notification_target_json)
    if not target:
        return

    if line_token := target.get("line_token"):
        await _send_line_notify(line_token, full_message)

    email = target.get("email")
    if email and target.get("email_enabled", True) is False:
        email = None
    if email:
        import asyncio
        await asyncio.to_thread(
            _send_email_sync, email, email_subject, full_message,
        )


async def send_test_notification(
    db: AsyncSession,
    device_id: str,
) -> dict:
    """
    Phase 21 テスト通知。subscribers の LINE / email にのみ送信する。
    電話は発信しない (TP社のデモ中の混乱回避)。

    Returns: { "line_sent": int, "email_sent": int, "phone_skipped": int }
    """
    import asyncio
    from datetime import datetime, timezone, timedelta

    subscribers = await _get_subscribers(db, device_id)
    if not subscribers:
        return {"line_sent": 0, "email_sent": 0, "phone_skipped": 0}

    jst = timezone(timedelta(hours=9))
    now_jst = datetime.now(jst).strftime("%Y/%m/%d %H:%M:%S JST")

    line_msg = (
        "【Leonardo Jr.】\n"
        "テスト通知です。\n"
        "動作確認のメッセージです。\n"
        f"送信時刻: {now_jst}"
    )
    email_subject = "[TEST]【Leonardo Jr.】動作確認テスト"
    email_body = (
        f"\nLeonardo Jr. テスト通知\n"
        f"動作確認のメッセージです。\n\n"
        f"デバイス: {device_id}\n"
        f"送信時刻: {now_jst}\n"
        f"\nこのメールはテスト通知ボタンから送信されました。\n"
        f"検知時の通知ではありません。\n"
    )

    line_subs = [s for s in subscribers if s.channel == "line"]
    email_subs = [s for s in subscribers if s.channel == "email"]
    phone_subs = [s for s in subscribers if s.channel == "phone"]

    line_sent = 0
    for s in line_subs:
        if await _send_line_message(s.target, line_msg):
            line_sent += 1

    email_sent = 0
    for s in email_subs:
        ok = await asyncio.to_thread(
            _send_email_sync, s.target, email_subject, email_body,
        )
        if ok:
            email_sent += 1

    return {
        "line_sent": line_sent,
        "email_sent": email_sent,
        "phone_skipped": len(phone_subs),
    }
