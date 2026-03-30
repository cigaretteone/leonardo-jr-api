#!/usr/bin/env python3
"""
lte_sender.py — Leonardo Jr. LTE送信モジュール
野外設置型省電力エッジAIデバイス用 イベント駆動型LTE送信

Phase 1.1対応: UUID v7 event_id, JSON POST, X-Api-Token認証, 冪等性保証

=== 関数リスト ===
  _run(cmd, timeout)
      subprocessを実行し (returncode, stdout, stderr) を返す内部ヘルパー。

  get_modem_index() -> Optional[str]
      mmcli -L でモデムインデックスを取得する。

  get_signal_quality(modem_index) -> Optional[int]
      mmcli -m でキャッシュ済み電波強度 (0–100%) を取得する。
      disable状態でも直近のキャッシュ値が返ることがある。

  enable_modem(modem_index) -> bool
      mmcli --enable でモデムをenable状態にする。
      "Invalid transition" エラーは既にenabled状態とみなして True を返す。

  connect_lte(modem_index) -> bool
      mmcli --simple-connect で APN 接続する (APN: ppsim.jp)。

  get_bearer_info(modem_index) -> Optional[dict]
      mmcli --bearer --output-json から IP/GW を取得する。

  setup_network_interface(bearer) -> bool
      wwan0 に IP アドレスを付与し、デフォルトルートを設定する。

  teardown_network_interface() -> None
      wwan0 の IP アドレス・ルートを削除してインターフェースをdown にする。

  disconnect_lte(modem_index) -> None
      mmcli --simple-disconnect で LTE 切断する（エラーは無視）。

  disable_modem(modem_index) -> None
      mmcli --disable でモデムを disable 状態にする（エラーは無視）。

  send_event_http(metadata, wwan_ip) -> SendResult
      Phase 1.1: JSON POST で JR API にイベントを送信する（指数バックオフ再試行）。
      SendResult(acked=True)  → 200/201 受理
      SendResult(acked=False, should_queue=False) → 永続エラー(4xx) リトライなし
      SendResult(acked=False, should_queue=True)  → 一時エラー → キュー保存

  save_to_local_queue(image_path, metadata) -> bool
      送信失敗イベントをローカルキュー (JSON ファイル) に保存する。
      metadata に event_id (UUID v7) が含まれるため再送時に同一 UUID で送信される。

  load_local_queue() -> list[Path]
      ローカルキューの JSON ファイルを古い順で返す。

  process_local_queue(limit) -> int
      LTE 接続済み状態で未送信キューを再送し、成功件数を返す（上限 limit 件）。
      永続エラー(4xx)のエントリはキューから削除してスキップ。

  send_event_with_lte(...) -> bool
      メインエントリポイント。検知イベントをLTE送信する。
      AI推論停止/再開フック (inference_pause/inference_resume) をサポート。

  main()
      使用例。--test 引数で単体テストを実行する。

=== 処理フロー (send_event_with_lte) ===
  [1] モデムインデックス取得
        └─ 失敗 → ローカルキュー保存して return False
  [2] 電波強度チェック（キャッシュ値使用、オプション）
        └─ 1–40% → ローカルキュー保存して return False（0% は不明扱いでスキップ除外）
  [3] AI推論停止フック呼び出し（inference_pause が設定されている場合）
  [4] モデム enable（Invalid transition → 既にenabled扱いで続行）
        └─ 失敗 → ローカルキュー保存して return False
  [5] APN 接続 simple-connect (ppsim.jp / pp@sim / jpn)
        └─ 失敗 → ローカルキュー保存して return False
  [6] Bearer 情報取得（IP / GW）
        └─ 失敗 → ローカルキュー保存して return False
  [7] wwan0 ネットワーク設定（IP 付与・デフォルトルート）
        └─ 失敗 → ローカルキュー保存して return False
  [8] 未送信キュー再送（最大 limit 件）
  [9] 本イベント HTTP POST 送信（最大 3 回リトライ）
        └─ 永続エラー(4xx) → ログのみ（キューに入れない）
        └─ 一時エラー      → ローカルキュー保存
  [10] finally: simple-disconnect → disable → ネットワーク削除 → 推論再開フック（常に実行）
"""

import json
import logging
import subprocess
import sys
import time
from collections import namedtuple
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import requests
import dns.resolver
import socket

_orig_getaddrinfo = socket.getaddrinfo

def _resolve_host(hostname):
    """Resolve via 8.8.8.8, bypass Tailscale DNS."""
    try:
        r = dns.resolver.Resolver(configure=False)
        r.nameservers = ["8.8.8.8"]
        r.lifetime = 10
        return str(r.resolve(hostname, "A")[0])
    except Exception:
        return hostname
from requests.adapters import HTTPAdapter
from urllib3.poolmanager import PoolManager


# ===========================================================
# 定数定義（環境に合わせて変更してください）
# ===========================================================

# Phase 1.1: JR API のみに統一（旧 ENDPOINT_URL / BEARER_TOKEN 廃止）
DEVICE_ID = "LJ-671493E4-QDSF"
API_TOKEN = "m0lCjXhKXBooGZ87ty_ASxbIQh0iD_MQwrYC-CVYuNU"
JR_API_URL_TEMPLATE = "https://leonardo-jr-api.onrender.com/api/v1/devices/{}/event"

APN = "ppsim.jp"
APN_USER = "pp@sim"
APN_PASSWORD = "jpn"
NETWORK_INTERFACE = "wwan0"

LTE_QUEUE_DIR = Path("/home/manta/leonardo_jr/lte_queue")
LTE_QUEUE_MAX = 100

CONNECT_TIMEOUT_SEC = 30    # LTE 接続タイムアウト（秒）
SEND_TIMEOUT_SEC = 30       # HTTP 送信タイムアウト（秒）
MAX_RETRY = 3               # HTTP 送信最大再試行回数
BASE_RETRY_DELAY = 2        # 指数バックオフ基本待機秒数 (delay = BASE * 2^attempt)
SIGNAL_SKIP_THRESHOLD = 40  # 電波強度スキップ閾値（%）

class SourceIPAdapter(HTTPAdapter):
    def __init__(self, source_ip, **kwargs):
        self._source_ip = source_ip
        super().__init__(**kwargs)

    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        pool_kwargs["source_address"] = (self._source_ip, 0)
        return super().init_poolmanager(connections, maxsize, block, **pool_kwargs)

# ===========================================================
# ロギング設定（INFO 以上: 成功 / 失敗のみ記録）
# ===========================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)

# テストで patch するためのモジュール名（直接実行時は "__main__"）
_MODULE = __name__


# ===========================================================
# 送信結果（Phase 1.1）
# ===========================================================

SendResult = namedtuple("SendResult", ["acked", "should_queue"])
"""
acked:        True  = サーバ受理済み (200 duplicate / 201 accepted)
should_queue: True  = 一時エラー → キュー保存して次回再送
              False = 永続エラー (4xx) → キュー保存しない
"""


# ===========================================================
# 内部ヘルパー
# ===========================================================

def _run(cmd: list[str], timeout: int = 30) -> tuple[int, str, str]:
    """
    subprocess コマンドを実行して (returncode, stdout, stderr) を返す。
    タイムアウトまたはコマンド未検出時は returncode=-1 を返す。
    """
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", "timeout"
    except FileNotFoundError:
        return -1, "", f"command not found: {cmd[0]}"


# ===========================================================
# モデム操作関数
# ===========================================================

def get_modem_index() -> Optional[str]:
    """
    mmcli -L でモデムインデックスを取得する。
    # mmcli -L: 約50mA × 1秒 ≈ 0.01mAh
    Returns: インデックス文字列（例: "0"）、失敗時 None
    """
    rc, out, _ = _run(["mmcli", "-L"])
    if rc != 0 or not out:
        return None
    # 出力例: "/org/freedesktop/ModemManager1/Modem/0 [Simcom] SIMCOM_SIM7600G-H"
    for line in out.splitlines():
        if "/Modem/" in line:
            idx = line.split("/Modem/")[-1].split()[0].rstrip(":")
            if idx.isdigit():
                return idx
    return None


def get_signal_quality(modem_index: str) -> Optional[int]:
    """
    mmcli -m {idx} でキャッシュ済み電波強度（%）を取得する。
    disable 状態でも直近のキャッシュ値が返ることがある。
    0% は disable 直後の不明値として返ることがあるため、呼び出し元で除外する。
    # mmcli -m: 約50mA × 1秒 ≈ 0.01mAh
    Returns: 電波強度 0–100、取得不可の場合 None
    """
    rc, out, _ = _run(["mmcli", "-m", modem_index])
    if rc != 0:
        return None
    for line in out.splitlines():
        if "signal quality" in line.lower():
            # 例: "| signal quality: '75' (recent)"
            parts = line.split("'")
            for part in parts:
                if part.strip().isdigit():
                    return int(part.strip())
    return None


def enable_modem(modem_index: str) -> bool:
    """
    mmcli --enable でモデムを enable 状態にする。
    "Invalid transition" エラーは既に enabled 状態とみなして True を返す。
    # enable: 平均200mA × 5秒 ≈ 0.28mAh
    Returns: 成功（または既に enabled）の場合 True
    """
    rc, _, err = _run(["mmcli", "-m", modem_index, "--enable"], timeout=30)
    if rc == 0:
        return True
    # 大文字小文字を問わず "invalid transition" は既に enabled → 続行可能
    if "invalid" in err.lower() and "transition" in err.lower():
        return True
    return False


def connect_lte(modem_index: str) -> bool:
    """
    mmcli --simple-connect で APN 接続する。
    # simple-connect: 平均300mA × 15秒 ≈ 1.25mAh
    Returns: 接続成功時 True
    """
    rc, _, _ = _run(
        [
            "mmcli", "-m", modem_index,
            "--simple-connect",
            f"apn={APN},user={APN_USER},password={APN_PASSWORD},allowed-auth=pap,ip-type=ipv4",
        ],
        timeout=CONNECT_TIMEOUT_SEC,
    )
    return rc == 0


def get_bearer_info(modem_index: str) -> Optional[dict]:
    """
    接続済み Bearer から IP アドレスとゲートウェイを取得する。
    モデム情報から Bearer パスを特定し、Bearer の JSON 詳細を解析する。
    # mmcli bearer query: 約50mA × 2秒 ≈ 0.03mAh
    Returns: {"ip": str, "prefix": str, "gateway": str}、取得不可の場合 None
    """
    # モデム情報から Bearer パスを取得
    rc, out, _ = _run(["mmcli", "-m", modem_index, "--output-json"])
    if rc != 0:
        return None
    try:
        modem_data = json.loads(out)
        bearers: list = (
            modem_data.get("modem", {})
            .get("generic", {})
            .get("bearers", [])
        )
        if not bearers:
            return None
        # simple-connect で最後に作成された Bearer（末尾）を使用
        bearer_path: str = bearers[-1]
        bearer_idx = bearer_path.rstrip("/").split("/")[-1]
    except (json.JSONDecodeError, KeyError, IndexError):
        return None

    # Bearer の詳細を取得
    rc2, out2, _ = _run(["mmcli", "--bearer", bearer_idx, "--output-json"])
    if rc2 != 0:
        return None
    try:
        bearer_data = json.loads(out2)
        ipv4: dict = bearer_data.get("bearer", {}).get("ipv4-config", {})
        ip = ipv4.get("address", "")
        prefix = str(ipv4.get("prefix", "32"))
        gateway = ipv4.get("gateway", "")
        if not ip or not gateway:
            return None
        return {"ip": ip, "prefix": prefix, "gateway": gateway}
    except (json.JSONDecodeError, KeyError):
        return None


def setup_network_interface(bearer: dict) -> bool:
    ip = bearer["ip"]
    prefix = bearer["prefix"]

    gateway = bearer["gateway"]
    # Set interface UP
    _run(["ip", "link", "set", NETWORK_INTERFACE, "up"])

    # Set IP Address
    _run(["ip", "addr", "flush", "dev", NETWORK_INTERFACE])
    _run(["ip", "addr", "add", f"{ip}/{prefix}", "dev", NETWORK_INTERFACE])

    # Policy routing: wwan0 table 100, eth0 untouched
    _run(["ip", "route", "flush", "table", "100"])
    _run(["ip", "route", "add", "default", "via", gateway, "dev", NETWORK_INTERFACE, "table", "100"])
    # Clean all old table 100 rules
    for _ in range(20):
        rc, _, _ = _run(["ip", "rule", "del", "table", "100"], timeout=3)
        if rc != 0:
            break
    _run(["ip", "rule", "add", "from", ip, "table", "100"])

    return True


def teardown_network_interface() -> None:
    """
    wwan0 のデフォルトルート・IP アドレスを削除してインターフェースを down にする。
    エラーは無視する（既に削除済みの場合も想定）。
    # ip route del / addr flush / link down: 約5mA × 2秒 ≈ 0.003mAh
    """
    _run(["ip", "route", "flush", "table", "100"])
    for _ in range(20):
        rc, _, _ = _run(["ip", "rule", "del", "table", "100"], timeout=3)
        if rc != 0:
            break
    _run(["ip", "route", "del", "default", "dev", NETWORK_INTERFACE])
    _run(["ip", "addr", "flush", "dev", NETWORK_INTERFACE])
    _run(["ip", "link", "set", NETWORK_INTERFACE, "down"])


def disconnect_lte(modem_index: str) -> None:
    """
    mmcli --simple-disconnect で LTE 切断する（エラーは無視）。
    # simple-disconnect: 平均200mA × 3秒 ≈ 0.17mAh
    """
    _run(["mmcli", "-m", modem_index, "--simple-disconnect"], timeout=15)


def disable_modem(modem_index: str) -> None:
    """
    mmcli --disable でモデムを disable 状態にする（エラーは無視）。
    # disable: 平均150mA × 3秒 ≈ 0.13mAh
    """
    _run(["mmcli", "-m", modem_index, "--disable"], timeout=15)


# ===========================================================
# HTTP 送信関数 (Phase 1.1)
# ===========================================================

def send_event_http(metadata: dict, wwan_ip: str) -> SendResult:
    """
    Phase 1.1: JSON POST で JR API にイベントを送信する。

    ペイロード: DetectionEventRequest スキーマ準拠
    認証: X-Api-Token ヘッダ
    冪等性: event_id (UUID v7) によりサーバ側で ON CONFLICT DO NOTHING

    ACK判定:
      200, 201        → acked=True  (201=新規受理, 200=重複=既に受理済み)
      400,401,403,422 → acked=False, should_queue=False (永続エラー、リトライしない)
      429             → Retry-After ヘッダ尊重してリトライ
      500,502,503,504 → acked=False, should_queue=True  (一時エラー、指数バックオフ)
      接続エラー/Timeout → same as 5xx

    Returns: SendResult(acked, should_queue)
    """
    device_id = metadata.get("device_id", DEVICE_ID)
    url = JR_API_URL_TEMPLATE.format(device_id)

    # DNS patch (Tailscale 干渉回避)
    from urllib.parse import urlparse
    _parsed = urlparse(url)
    _resolved = _resolve_host(_parsed.hostname)
    logger.info("DNS resolved %s -> %s", _parsed.hostname, _resolved)

    def _patched_gai(host, port, family=0, type=0, proto=0, flags=0):
        if host == _parsed.hostname:
            host = _resolved
        return _orig_getaddrinfo(host, port, family, type, proto, flags)

    socket.getaddrinfo = _patched_gai

    session = requests.Session()
    session.mount("https://", SourceIPAdapter(wwan_ip))
    session.mount("http://", SourceIPAdapter(wwan_ip))

    # Phase 1.1 ペイロード構築
    detection_type = metadata.get("detection_type")
    confidence = metadata.get("confidence", 0.0)
    payload = {
        "event_id": metadata.get("event_id"),
        "device_id": device_id,
        "event_type": metadata.get("event_type", "detection"),
        "occurred_at": metadata.get("occurred_at"),
        "detection": {
            "class": detection_type,
            "confidence": float(confidence),
            "distance_estimate": None,
        } if detection_type else None,
        "thumbnail_b64": metadata.get("thumbnail_b64"),
        "gps": None,
        "device_status": None,
    }

    try:
        for attempt in range(MAX_RETRY):
            headers = {
                "X-Api-Token": API_TOKEN,
                "Content-Type": "application/json",
                "X-Retry-Count": str(attempt),
            }
            try:
                resp = session.post(
                    url,
                    json=payload,
                    headers=headers,
                    timeout=SEND_TIMEOUT_SEC,
                )

                if resp.status_code in (200, 201):
                    logger.info(
                        "送信成功 event_id=%s status=%d",
                        payload["event_id"], resp.status_code,
                    )
                    # Phase 2.1: parse video_requested
                    try:
                        resp_json = resp.json()
                        metadata['_video_requested'] = resp_json.get('video_requested', False)
                        metadata['_upload_url'] = resp_json.get('upload_url')
                    except Exception:
                        metadata['_video_requested'] = False
                        metadata['_upload_url'] = None
                    return SendResult(acked=True, should_queue=False)

                elif resp.status_code in (400, 401, 403, 422):
                    logger.error(
                        "永続エラー %d: リトライしない event_id=%s body=%s",
                        resp.status_code, payload["event_id"], resp.text[:200],
                    )
                    return SendResult(acked=False, should_queue=False)

                elif resp.status_code == 429:
                    retry_after = int(
                        resp.headers.get("Retry-After", BASE_RETRY_DELAY * (2 ** attempt))
                    )
                    logger.warning(
                        "429 Too Many Requests: %d秒後リトライ (attempt %d/%d)",
                        retry_after, attempt + 1, MAX_RETRY,
                    )
                    if attempt < MAX_RETRY - 1:
                        time.sleep(retry_after)

                elif resp.status_code in (500, 502, 503, 504):
                    delay = BASE_RETRY_DELAY * (2 ** attempt)
                    logger.warning(
                        "サーバーエラー %d: %d秒後リトライ (attempt %d/%d)",
                        resp.status_code, delay, attempt + 1, MAX_RETRY,
                    )
                    if attempt < MAX_RETRY - 1:
                        time.sleep(delay)

                else:
                    logger.error(
                        "予期しないステータス %d: キュー保存して終了", resp.status_code,
                    )
                    return SendResult(acked=False, should_queue=True)

            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
                delay = BASE_RETRY_DELAY * (2 ** attempt)
                logger.warning(
                    "通信エラー: %s — %d秒後リトライ (attempt %d/%d)",
                    e, delay, attempt + 1, MAX_RETRY,
                )
                if attempt < MAX_RETRY - 1:
                    time.sleep(delay)

        logger.error("最大リトライ回数到達 (%d回): キュー保存", MAX_RETRY)
        return SendResult(acked=False, should_queue=True)

    finally:
        socket.getaddrinfo = _orig_getaddrinfo


# ===========================================================


# ===========================================================
# Phase 2.1: Video upload
# ===========================================================

def upload_video_http(upload_url, video_path, sha256_hex, wwan_ip, codec="h265", resolution="480p", duration_sec=None):
    """Upload video file to server. Returns SendResult."""
    from urllib.parse import urlparse
    _parsed = urlparse(upload_url)
    _resolved = _resolve_host(_parsed.hostname)
    def _patched_gai(host, port, family=0, type=0, proto=0, flags=0):
        if host == _parsed.hostname:
            host = _resolved
        return _orig_getaddrinfo(host, port, family, type, proto, flags)
    socket.getaddrinfo = _patched_gai
    try:
        session = requests.Session()
        session.mount("https://", SourceIPAdapter(wwan_ip))
        headers = {
            "X-Api-Token": API_TOKEN,
            "Content-Type": "application/octet-stream",
            "X-SHA256": sha256_hex,
            "X-Codec": codec,
            "X-Resolution": resolution,
        }
        if duration_sec is not None:
            headers["X-Duration-Sec"] = str(duration_sec)
        with open(video_path, "rb") as f:
            video_data = f.read()
        for attempt in range(MAX_RETRY):
            headers["X-Retry-Count"] = str(attempt)
            try:
                resp = session.post(upload_url, data=video_data, headers=headers, timeout=60)
                if resp.status_code in (200, 201):
                    logger.info("Video upload success: %d", resp.status_code)
                    return SendResult(acked=True, should_queue=False)
                elif resp.status_code in (400, 404, 413, 403):
                    logger.error("Video upload permanent error %d: %s", resp.status_code, resp.text[:200])
                    return SendResult(acked=False, should_queue=False)
                else:
                    delay = BASE_RETRY_DELAY * (2 ** attempt)
                    logger.warning("Video upload retry %d/%d (status=%d)", attempt+1, MAX_RETRY, resp.status_code)
                    time.sleep(delay)
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
                delay = BASE_RETRY_DELAY * (2 ** attempt)
                logger.warning("Video upload retry %d/%d (%s)", attempt+1, MAX_RETRY, e)
                time.sleep(delay)
        return SendResult(acked=False, should_queue=True)
    finally:
        socket.getaddrinfo = _orig_getaddrinfo

# [Dual Reporting 廃止] send_to_jr_api — Phase 1.1 で不要になったため無効化
# 将来参照用にコードを残す
# ===========================================================

# def send_to_jr_api(metadata: dict, wwan_ip: str) -> None:
#     """
#     [廃止] Dual Reporting: 旧レシーバと新 JR API の両方に送信していた関数。
#     Phase 1.1 で send_event_http が JR API に直接送信するため不要。
#     """
#     try:
#         device_id = JR_DEVICE_ID
#         url = JR_API_URL_TEMPLATE.format(device_id)
#         ...
#     except Exception as e:
#         logger.error(f"JR_API: Exception {e}")


# ===========================================================
# ローカルキュー管理関数
# ===========================================================


def save_to_local_queue(image_path: str, metadata: dict) -> bool:
    """
    送信失敗イベントをローカルキュー（JSON ファイル）に保存する。
    ファイル名: {queued_at_usec}_{event_id_prefix}.json
    metadata に event_id (UUID v7) が含まれるため、再送時に同一 UUID を使用可能。
    上限（LTE_QUEUE_MAX 件）を超えた場合、最古のファイルを削除する。
    Returns: 保存成功時 True
    """
    try:
        LTE_QUEUE_DIR.mkdir(parents=True, exist_ok=True)

        queued_ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        # event_id をファイル名に含めて追跡しやすくする
        event_id_part = str(metadata.get("event_id", queued_ts))[:20].replace("-", "")
        filename = f"{queued_ts}_{event_id_part}.json"
        queue_entry = {
            "queued_at": datetime.now(timezone.utc).isoformat(),
            "image_path": image_path,
            "metadata": metadata,
        }
        (LTE_QUEUE_DIR / filename).write_text(
            json.dumps(queue_entry, ensure_ascii=False), encoding="utf-8"
        )

        # 上限超過チェック（最古から削除）
        existing = sorted(LTE_QUEUE_DIR.glob("*.json"))
        overflow = len(existing) - LTE_QUEUE_MAX
        for old_file in existing[:max(0, overflow)]:
            old_file.unlink(missing_ok=True)

        return True
    except OSError as e:
        logger.error("キュー保存失敗: %s", e)
        return False


def load_local_queue() -> list[Path]:
    """
    ローカルキューの JSON ファイルを古い順（ファイル名昇順）で返す。
    Returns: Path のリスト（キュー未作成または空の場合は空リスト）
    """
    if not LTE_QUEUE_DIR.exists():
        return []
    return sorted(LTE_QUEUE_DIR.glob("*.json"))


def process_local_queue(limit: int = 3, wwan_ip: str = "") -> int:
    """
    未送信キューを再送する（LTE 接続済み・ネットワーク設定済みの状態で呼ぶこと）。
    1 回の LTE セッションで処理する件数を limit で制限する（デフォルト 3 件）。

    送信成功 → キューファイル削除
    永続エラー(4xx) → キューファイル削除（再送しても無意味なため）
    一時エラー → キューファイル保持（次回セッションに持ち越し）
    破損 JSON → キューファイル削除してスキップ

    event_id は metadata に保存済みのため、再送時も同一 UUID で冪等送信される。

    # 1件あたり: 平均300mA × 10秒 ≈ 0.83mAh（limit=3 で最大 ≈ 2.49mAh）
    Args:
        limit   : 1回のセッションで再送する最大件数（デフォルト 3）
        wwan_ip : wwan0 の IP アドレス（SourceIPAdapter 用）
    Returns: 送信成功件数
    """
    queue_files = load_local_queue()
    total = len(queue_files)
    target = queue_files[:limit]
    sent_count = 0

    for qfile in target:
        try:
            entry = json.loads(qfile.read_text(encoding="utf-8"))
            meta = entry.get("metadata", {})
        except (json.JSONDecodeError, OSError):
            qfile.unlink(missing_ok=True)
            continue

        result = send_event_http(meta, wwan_ip)

        if result.acked:
            qfile.unlink(missing_ok=True)
            sent_count += 1
        elif not result.should_queue:
            # 永続エラー(4xx): 再送しても無意味なのでキューから削除
            logger.error("永続エラーのためキュー削除: %s", qfile.name)
            qfile.unlink(missing_ok=True)
        # else: 一時エラー → キューに残して次回再試行

    remaining = total - limit
    if remaining > 0:
        logger.warning("キュー残り %d 件（次回持ち越し）", remaining)

    return sent_count


# ===========================================================
# メインエントリポイント
# ===========================================================

def send_event_with_lte(
    image_path: str,
    event_type: str = "detection",
    detection_type: str = "bear",
    confidence: float = 0.0,
    occurred_at: Optional[str] = None,
    inference_pause: Optional[Callable[[], None]] = None,
    inference_resume: Optional[Callable[[], None]] = None,
) -> bool:
    """
    検知イベント発生時の LTE 送信エントリポイント。
    いかなる例外が発生してもメインの推論ループを止めない。

    UUID v7 をデバイス側で生成し、event_id として送信する（冪等性保証）。
    image_base64 は Phase 2 で対応予定（現在は送信しない）。

    === 消費電力内訳（1 イベント、キュー 0 件の場合）===
    # [1] モデム検出 (mmcli -L):         約 50mA ×  1秒 ≈ 0.01mAh
    # [2] 電波強度確認 (mmcli -m):       約 50mA ×  1秒 ≈ 0.01mAh
    # [3] enable (mmcli --enable):        約200mA ×  5秒 ≈ 0.28mAh
    # [4] connect (simple-connect):       約300mA × 15秒 ≈ 1.25mAh
    # [5] Bearer 取得・ネットワーク設定: 約 50mA ×  3秒 ≈ 0.04mAh
    # [6] HTTP POST (JSON):               約300mA × 10秒 ≈ 0.83mAh
    # [7] disconnect (simple-disconnect): 約200mA ×  3秒 ≈ 0.17mAh
    # [8] disable (mmcli --disable):      約150mA ×  3秒 ≈ 0.13mAh
    # ──────────────────────────────────────────────────────────
    # 合計（キュー 0 件）:                              ≈ 2.72mAh
    # ← 制約 6mAh 以内（キュー limit=3 件追加でも ≈ 5.21mAh）

    Args:
        image_path      : 検知画像パス（Phase 2 で使用予定、現在は保存のみ）
        event_type      : イベント種別（デフォルト: "detection"）
        detection_type  : 検知クラス（"bear" / "human" / "vehicle" / "unknown"）
        confidence      : 検出信頼度 0.0–1.0
        occurred_at     : ISO8601 タイムスタンプ（None で自動生成）
        inference_pause : LTE 通信前に呼ぶ AI 推論停止コールバック（省略可）
        inference_resume: LTE 通信後に呼ぶ AI 推論再開コールバック（省略可）
    Returns:
        送信成功時 True、スキップ / 失敗時 False
    """
    # UUID v7 生成（RFC 9562 準拠、時刻単調増加でソート可能）
    try:
        import uuid_utils as _uuid_utils
        event_id = _uuid_utils.uuid7()
    except ImportError:
        import uuid as _uuid
        event_id = _uuid.uuid4()
        logger.warning("uuid_utils 未インストール: uuid4 で代替 (pip install uuid_utils)")

    if occurred_at is None:
        occurred_at = datetime.now(timezone.utc).isoformat()

    # Phase 2.1: generate thumbnail
    _thumbnail_b64 = None
    try:
        from thumbnail_capture import thumbnail_from_file
        _thumbnail_b64 = thumbnail_from_file(image_path)
        if _thumbnail_b64:
            logger.info("Thumbnail generated: %d chars", len(_thumbnail_b64))
    except Exception as te:
        logger.warning("Thumbnail generation skipped: %s", te)

    metadata: dict = {
        "event_id": str(event_id),
        "device_id": DEVICE_ID,
        "event_type": event_type,
        "occurred_at": occurred_at,
        "detection_type": detection_type,
        "confidence": round(float(confidence), 4),
        "thumbnail_b64": _thumbnail_b64,
    }

    modem_index: Optional[str] = None

    try:
        # [1] モデムインデックス取得
        modem_index = get_modem_index()
        if modem_index is None:
            logger.error("モデム未検出: キュー保存して終了")
            save_to_local_queue(image_path, metadata)
            return False

        # [2] 電波強度チェック（disable 状態のキャッシュ値を使用）
        # 0% は disable 直後の不明値のためスキップ判定から除外する
        signal = get_signal_quality(modem_index)
        if signal is not None and signal > 0 and signal <= SIGNAL_SKIP_THRESHOLD:
            logger.warning(
                "電波強度 %d%% <= %d%%: キュー保存してスキップ",
                signal, SIGNAL_SKIP_THRESHOLD,
            )
            save_to_local_queue(image_path, metadata)
            return False

        # [3] AI 推論停止フック（LTE 通信中は推論を停止する）
        if inference_pause:
            inference_pause()

        # [4] モデム enable
        if not enable_modem(modem_index):
            logger.error("モデム enable 失敗: キュー保存して終了")
            save_to_local_queue(image_path, metadata)
            return False

        # [5] APN 接続
        if not connect_lte(modem_index):
            logger.error("LTE 接続失敗: キュー保存して終了")
            save_to_local_queue(image_path, metadata)
            return False

        # [6a] Bearer 情報取得
        bearer = get_bearer_info(modem_index)
        if bearer is None:
            logger.error("Bearer 情報取得失敗: キュー保存して終了")
            save_to_local_queue(image_path, metadata)
            return False

        # [6b] ネットワーク設定（IP 付与・デフォルトルート）
        if not setup_network_interface(bearer):
            logger.error("ネットワーク設定失敗: キュー保存して終了")
            save_to_local_queue(image_path, metadata)
            return False

        # [7] 未送信キューを先に再送（最大 limit=3 件）
        pending = load_local_queue()
        if pending:
            sent = process_local_queue(wwan_ip=bearer["ip"])
            logger.info("キュー再送: %d 件成功（キュー残: %d 件）", sent, len(pending) - sent)

        # [8] 本イベント送信（event_id は metadata に含まれる）
        result = send_event_http(metadata, bearer["ip"])

        if result.acked:
            logger.info(
                "送信成功: event_id=%s detection_type=%s confidence=%.2f",
                metadata["event_id"], detection_type, confidence,
            )
            # Phase 2.1: video upload if requested
            if metadata.get('_video_requested') and metadata.get('_upload_url'):
                try:
                    from video_slicer import slice_video, cleanup_slice
                    v_path, v_sha, v_size = slice_video(metadata['event_id'])
                    if v_path and v_sha:
                        logger.info('Video ready: %s (%d bytes)', v_path, v_size)
                        v_result = upload_video_http(
                            metadata['_upload_url'], v_path, v_sha, bearer['ip']
                        )
                        if v_result.acked:
                            logger.info('Video uploaded: event_id=%s', metadata['event_id'])
                            cleanup_slice(metadata['event_id'])
                        else:
                            logger.warning('Video upload failed: event_id=%s', metadata['event_id'])
                    else:
                        logger.warning('Video slice failed, skipping upload')
                except Exception as ve:
                    logger.error('Video pipeline error: %s', ve)
            return True
        elif result.should_queue:
            logger.error("送信失敗（一時エラー）: キュー保存")
            save_to_local_queue(image_path, metadata)
            return False
        else:
            logger.error(
                "送信失敗（永続エラー）: event_id=%s キュー保存しない",
                metadata["event_id"],
            )
            return False

    except Exception as e:  # pylint: disable=broad-except
        # いかなる例外もメイン推論ループを止めない
        logger.error("LTE 送信で予期しない例外: %s", e)
        try:
            save_to_local_queue(image_path, metadata)
        except Exception:  # pylint: disable=broad-except
            pass
        return False

    finally:
        # [9] 切断・disable・ネットワーク削除（常に実行）
        if modem_index is not None:
            disconnect_lte(modem_index)
            disable_modem(modem_index)
        teardown_network_interface()
        # AI 推論再開フック（LTE 通信完了後に推論を再開する）
        if inference_resume:
            inference_resume()


# ===========================================================
# 単体テスト（モック使用、LTE 未接続環境で実行可能）
# ===========================================================

def _run_tests() -> None:
    """
    モックを使った単体テスト。
    実行: python lte_sender.py --test
    """
    import shutil
    import tempfile
    import unittest
    from unittest.mock import MagicMock, call, patch

    MOD = _MODULE  # "__main__" (直接実行時) / "lte_sender" (import 時)

    class TestLteSender(unittest.TestCase):

        def setUp(self) -> None:
            self.tmpdir = Path(tempfile.mkdtemp())
            self.image_path = str(self.tmpdir / "test.jpg")
            # 最小 JPEG ファイル（FFD8 ヘッダ付き）
            with open(self.image_path, "wb") as f:
                f.write(b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x00" * 128)

        def tearDown(self) -> None:
            shutil.rmtree(self.tmpdir, ignore_errors=True)

        # ── get_modem_index ──────────────────────────────────

        def test_get_modem_index_success(self) -> None:
            out = "/org/freedesktop/ModemManager1/Modem/0 [Simcom] SIMCOM_SIM7600G-H\n"
            with patch("subprocess.run") as m:
                m.return_value = MagicMock(returncode=0, stdout=out, stderr="")
                self.assertEqual(get_modem_index(), "0")

        def test_get_modem_index_failure(self) -> None:
            with patch("subprocess.run") as m:
                m.return_value = MagicMock(returncode=1, stdout="", stderr="error")
                self.assertIsNone(get_modem_index())

        # ── get_signal_quality ───────────────────────────────

        def test_get_signal_quality_found(self) -> None:
            out = "  Status   | signal quality: '75' (recent)\n"
            with patch("subprocess.run") as m:
                m.return_value = MagicMock(returncode=0, stdout=out, stderr="")
                self.assertEqual(get_signal_quality("0"), 75)

        def test_get_signal_quality_not_found(self) -> None:
            with patch("subprocess.run") as m:
                m.return_value = MagicMock(returncode=0, stdout="no info", stderr="")
                self.assertIsNone(get_signal_quality("0"))

        # ── enable_modem ─────────────────────────────────────

        def test_enable_modem_success(self) -> None:
            with patch("subprocess.run") as m:
                m.return_value = MagicMock(returncode=0, stdout="", stderr="")
                self.assertTrue(enable_modem("0"))

        def test_enable_modem_invalid_transition_uppercase(self) -> None:
            """大文字 "Invalid transition" でも True を返す"""
            with patch("subprocess.run") as m:
                m.return_value = MagicMock(
                    returncode=1, stdout="", stderr="Error: Invalid transition"
                )
                self.assertTrue(enable_modem("0"))

        def test_enable_modem_invalid_transition_lowercase(self) -> None:
            """小文字 "invalid transition" でも True を返す"""
            with patch("subprocess.run") as m:
                m.return_value = MagicMock(
                    returncode=1, stdout="", stderr="error: invalid transition"
                )
                self.assertTrue(enable_modem("0"))

        def test_enable_modem_hard_failure(self) -> None:
            with patch("subprocess.run") as m:
                m.return_value = MagicMock(returncode=1, stdout="", stderr="fatal error")
                self.assertFalse(enable_modem("0"))

        # ── save_to_local_queue / load_local_queue ───────────

        def test_save_and_load_queue(self) -> None:
            test_queue = self.tmpdir / "queue"
            meta = {
                "event_id": "019cb000-0000-7000-0000-000000000001",
                "occurred_at": "2024-01-01T00:00:00+00:00",
                "device_id": DEVICE_ID,
            }
            with patch(f"{MOD}.LTE_QUEUE_DIR", test_queue):
                self.assertTrue(save_to_local_queue(self.image_path, meta))
                files = load_local_queue()
                self.assertEqual(len(files), 1)
                entry = json.loads(files[0].read_text())
                self.assertEqual(entry["image_path"], self.image_path)
                # event_id がキュー内に保存されていることを確認
                self.assertEqual(entry["metadata"]["event_id"], meta["event_id"])

        def test_queue_max_limit_deletes_oldest(self) -> None:
            test_queue = self.tmpdir / "queue"
            meta = {"event_id": "019cb000-0000-7000-0000-000000000001"}
            with patch(f"{MOD}.LTE_QUEUE_DIR", test_queue), \
                 patch(f"{MOD}.LTE_QUEUE_MAX", 3):
                for _ in range(5):
                    time.sleep(0.005)
                    save_to_local_queue(self.image_path, meta)
                self.assertLessEqual(len(load_local_queue()), 3)

        # ── process_local_queue ──────────────────────────────

        def _make_queue_files(self, count: int) -> Path:
            """テスト用キューディレクトリに count 件のエントリを作成する"""
            test_queue = self.tmpdir / "queue"
            meta = {
                "event_id": "019cb000-0000-7000-0000-000000000001",
                "device_id": DEVICE_ID,
                "event_type": "detection",
                "occurred_at": "2024-01-01T00:00:00+00:00",
                "detection_type": "bear",
                "confidence": 0.9,
            }
            with patch(f"{MOD}.LTE_QUEUE_DIR", test_queue):
                for _ in range(count):
                    time.sleep(0.005)
                    save_to_local_queue(self.image_path, meta)
            return test_queue

        def test_process_local_queue_respects_limit(self) -> None:
            """limit=2 のとき 2 件しか処理しない"""
            test_queue = self._make_queue_files(5)
            ok = SendResult(acked=True, should_queue=False)
            with patch(f"{MOD}.LTE_QUEUE_DIR", test_queue), \
                 patch(f"{MOD}.send_event_http", return_value=ok):
                sent = process_local_queue(limit=2)
            self.assertEqual(sent, 2)
            # 残り 3 件がキューに残っている
            self.assertEqual(len(sorted(test_queue.glob("*.json"))), 3)

        def test_process_local_queue_warns_remaining(self) -> None:
            """limit より多くのキューがある場合に warning を出す"""
            test_queue = self._make_queue_files(5)
            ok = SendResult(acked=True, should_queue=False)
            with patch(f"{MOD}.LTE_QUEUE_DIR", test_queue), \
                 patch(f"{MOD}.send_event_http", return_value=ok), \
                 self.assertLogs(level="WARNING") as log_ctx:
                process_local_queue(limit=3)
            self.assertTrue(
                any("キュー残り" in msg and "次回持ち越し" in msg for msg in log_ctx.output)
            )

        def test_process_local_queue_no_warning_within_limit(self) -> None:
            """キュー件数が limit 以下なら warning を出さない"""
            test_queue = self._make_queue_files(2)
            ok = SendResult(acked=True, should_queue=False)
            with patch(f"{MOD}.LTE_QUEUE_DIR", test_queue), \
                 patch(f"{MOD}.send_event_http", return_value=ok):
                with self.assertRaises(AssertionError):
                    with self.assertLogs(level="WARNING"):
                        process_local_queue(limit=3)

        def test_process_local_queue_default_limit_3(self) -> None:
            """デフォルト limit=3 で 3 件を処理する"""
            test_queue = self._make_queue_files(5)
            ok = SendResult(acked=True, should_queue=False)
            with patch(f"{MOD}.LTE_QUEUE_DIR", test_queue), \
                 patch(f"{MOD}.send_event_http", return_value=ok):
                sent = process_local_queue(wwan_ip="10.0.0.1")  # limit=3 がデフォルト
            self.assertEqual(sent, 3)

        def test_process_local_queue_permanent_error_deletes_entry(self) -> None:
            """永続エラー(4xx) のエントリはキューから削除される"""
            test_queue = self._make_queue_files(2)
            perm_err = SendResult(acked=False, should_queue=False)
            with patch(f"{MOD}.LTE_QUEUE_DIR", test_queue), \
                 patch(f"{MOD}.send_event_http", return_value=perm_err):
                sent = process_local_queue(limit=2)
            self.assertEqual(sent, 0)
            # 永続エラーなのでキューから削除されている
            self.assertEqual(len(sorted(test_queue.glob("*.json"))), 0)

        def test_process_local_queue_temp_error_keeps_entry(self) -> None:
            """一時エラーのエントリはキューに残る"""
            test_queue = self._make_queue_files(2)
            temp_err = SendResult(acked=False, should_queue=True)
            with patch(f"{MOD}.LTE_QUEUE_DIR", test_queue), \
                 patch(f"{MOD}.send_event_http", return_value=temp_err):
                sent = process_local_queue(limit=2)
            self.assertEqual(sent, 0)
            # 一時エラーなのでキューに残っている
            self.assertEqual(len(sorted(test_queue.glob("*.json"))), 2)

        def test_process_local_queue_skips_corrupt_json(self) -> None:
            """破損 JSON は削除してスキップ"""
            test_queue = self.tmpdir / "queue"
            test_queue.mkdir()
            (test_queue / "20240101T000000000000_corrupt.json").write_text(
                "{ invalid json", encoding="utf-8"
            )
            with patch(f"{MOD}.LTE_QUEUE_DIR", test_queue):
                sent = process_local_queue(limit=3)
            self.assertEqual(sent, 0)
            self.assertEqual(len(list(test_queue.glob("*.json"))), 0)

        # ── send_event_http ──────────────────────────────────

        def _make_meta(self, event_id: str = "019cb000-0000-7000-0000-000000000001") -> dict:
            return {
                "event_id": event_id,
                "device_id": DEVICE_ID,
                "event_type": "detection",
                "occurred_at": "2024-01-01T00:00:00+00:00",
                "detection_type": "bear",
                "confidence": 0.92,
            }

        def test_http_success_201(self) -> None:
            """201 → acked=True, should_queue=False"""
            with patch("requests.Session") as MockSession:
                MockSession.return_value.post.return_value = MagicMock(
                    status_code=201, headers={}, text=""
                )
                result = send_event_http(self._make_meta(), "10.0.0.1")
            self.assertTrue(result.acked)
            self.assertFalse(result.should_queue)

        def test_http_success_200_duplicate(self) -> None:
            """200 (重複) → acked=True, should_queue=False"""
            with patch("requests.Session") as MockSession:
                MockSession.return_value.post.return_value = MagicMock(
                    status_code=200, headers={}, text=""
                )
                result = send_event_http(self._make_meta(), "10.0.0.1")
            self.assertTrue(result.acked)
            self.assertFalse(result.should_queue)

        def test_http_permanent_4xx_no_retry(self) -> None:
            """400/401/403/422 → acked=False, should_queue=False, リトライしない"""
            for code in (400, 401, 403, 422):
                with patch("requests.Session") as MockSession:
                    MockSession.return_value.post.return_value = MagicMock(
                        status_code=code, headers={}, text="error"
                    )
                    result = send_event_http(self._make_meta(), "10.0.0.1")
                    self.assertFalse(result.acked, f"code={code}")
                    self.assertFalse(result.should_queue, f"code={code}")
                    self.assertEqual(
                        MockSession.return_value.post.call_count, 1,
                        f"code={code} should not retry"
                    )

        def test_http_5xx_retries_then_success(self) -> None:
            """500 → リトライ → 201 成功"""
            with patch("requests.Session") as MockSession, patch("time.sleep"):
                MockSession.return_value.post.side_effect = [
                    MagicMock(status_code=500, headers={}, text="err"),
                    MagicMock(status_code=201, headers={}, text=""),
                ]
                result = send_event_http(self._make_meta(), "10.0.0.1")
            self.assertTrue(result.acked)
            self.assertEqual(MockSession.return_value.post.call_count, 2)

        def test_http_5xx_max_retry_exceeded(self) -> None:
            """5xx が MAX_RETRY 回続いたら should_queue=True"""
            with patch("requests.Session") as MockSession, patch("time.sleep"):
                MockSession.return_value.post.return_value = MagicMock(
                    status_code=503, headers={}, text="err"
                )
                result = send_event_http(self._make_meta(), "10.0.0.1")
            self.assertFalse(result.acked)
            self.assertTrue(result.should_queue)
            self.assertEqual(MockSession.return_value.post.call_count, MAX_RETRY)

        def test_http_429_respects_retry_after(self) -> None:
            """429 → Retry-After ヘッダを読んで sleep → 201 成功"""
            with patch("requests.Session") as MockSession, patch("time.sleep") as mock_sleep:
                MockSession.return_value.post.side_effect = [
                    MagicMock(status_code=429, headers={"Retry-After": "5"}, text=""),
                    MagicMock(status_code=201, headers={}, text=""),
                ]
                result = send_event_http(self._make_meta(), "10.0.0.1")
            self.assertTrue(result.acked)
            mock_sleep.assert_called_once_with(5)

        def test_http_connection_error_retry_then_success(self) -> None:
            """ConnectionError → リトライ → 201 成功"""
            with patch("requests.Session") as MockSession, patch("time.sleep"):
                MockSession.return_value.post.side_effect = [
                    requests.exceptions.ConnectionError(),
                    MagicMock(status_code=201, headers={}, text=""),
                ]
                result = send_event_http(self._make_meta(), "10.0.0.1")
            self.assertTrue(result.acked)
            self.assertEqual(MockSession.return_value.post.call_count, 2)

        def test_http_timeout_retry_then_success(self) -> None:
            """Timeout → リトライ → 201 成功"""
            with patch("requests.Session") as MockSession, patch("time.sleep"):
                MockSession.return_value.post.side_effect = [
                    requests.exceptions.Timeout(),
                    MagicMock(status_code=201, headers={}, text=""),
                ]
                result = send_event_http(self._make_meta(), "10.0.0.1")
            self.assertTrue(result.acked)

        def test_http_connection_error_max_retry(self) -> None:
            """ConnectionError が MAX_RETRY 回 → should_queue=True"""
            with patch("requests.Session") as MockSession, patch("time.sleep"):
                MockSession.return_value.post.side_effect = requests.exceptions.ConnectionError()
                result = send_event_http(self._make_meta(), "10.0.0.1")
            self.assertFalse(result.acked)
            self.assertTrue(result.should_queue)
            self.assertEqual(MockSession.return_value.post.call_count, MAX_RETRY)

        def test_http_payload_schema(self) -> None:
            """ペイロードが Phase 1.1 スキーマ準拠かチェック"""
            captured = {}

            def fake_post(url, json=None, headers=None, timeout=None):
                captured["payload"] = json
                captured["headers"] = headers
                return MagicMock(status_code=201, headers={}, text="")

            with patch("requests.Session") as MockSession:
                MockSession.return_value.post.side_effect = fake_post
                send_event_http(self._make_meta("019cb000-0000-7001-0000-000000000002"), "10.0.0.1")

            p = captured["payload"]
            self.assertEqual(p["event_id"], "019cb000-0000-7001-0000-000000000002")
            self.assertEqual(p["device_id"], DEVICE_ID)
            self.assertEqual(p["event_type"], "detection")
            self.assertIn("occurred_at", p)
            self.assertIsNotNone(p["detection"])
            self.assertEqual(p["detection"]["class"], "bear")
            self.assertAlmostEqual(p["detection"]["confidence"], 0.92)
            self.assertIsNone(p["gps"])
            self.assertIsNone(p["device_status"])

        def test_http_x_retry_count_header(self) -> None:
            """X-Retry-Count ヘッダが attempt 番号で送信されること"""
            captured_headers = []

            def fake_post(url, json=None, headers=None, timeout=None):
                captured_headers.append(headers.get("X-Retry-Count"))
                return MagicMock(status_code=503, headers={}, text="err")

            with patch("requests.Session") as MockSession, patch("time.sleep"):
                MockSession.return_value.post.side_effect = fake_post
                send_event_http(self._make_meta(), "10.0.0.1")

            self.assertEqual(captured_headers, ["0", "1", "2"])

        def test_http_x_api_token_header(self) -> None:
            """X-Api-Token ヘッダが正しく送信されること"""
            captured = {}

            def fake_post(url, json=None, headers=None, timeout=None):
                captured["headers"] = headers
                return MagicMock(status_code=201, headers={}, text="")

            with patch("requests.Session") as MockSession:
                MockSession.return_value.post.side_effect = fake_post
                send_event_http(self._make_meta(), "10.0.0.1")

            self.assertEqual(captured["headers"]["X-Api-Token"], API_TOKEN)
            self.assertEqual(captured["headers"]["Content-Type"], "application/json")

        # ── send_event_with_lte フルフロー ──────────────────

        def _bearer(self) -> dict:
            return {"ip": "10.0.0.1", "prefix": "28", "gateway": "10.0.0.14"}

        def _ok_result(self) -> SendResult:
            return SendResult(acked=True, should_queue=False)

        def _fail_result(self) -> SendResult:
            return SendResult(acked=False, should_queue=True)

        def _perm_err_result(self) -> SendResult:
            return SendResult(acked=False, should_queue=False)

        def test_full_flow_success(self) -> None:
            """正常フロー: 送信成功 → True を返す"""
            with patch(f"{MOD}.get_modem_index", return_value="0"), \
                 patch(f"{MOD}.get_signal_quality", return_value=80), \
                 patch(f"{MOD}.enable_modem", return_value=True), \
                 patch(f"{MOD}.connect_lte", return_value=True), \
                 patch(f"{MOD}.get_bearer_info", return_value=self._bearer()), \
                 patch(f"{MOD}.setup_network_interface", return_value=True), \
                 patch(f"{MOD}.load_local_queue", return_value=[]), \
                 patch(f"{MOD}.send_event_http", return_value=self._ok_result()), \
                 patch(f"{MOD}.disconnect_lte"), \
                 patch(f"{MOD}.disable_modem"), \
                 patch(f"{MOD}.teardown_network_interface"):
                self.assertTrue(
                    send_event_with_lte(self.image_path, detection_type="bear", confidence=0.95)
                )

        def test_full_flow_event_id_in_metadata(self) -> None:
            """send_event_http に渡される metadata に event_id が含まれること"""
            captured = {}

            def fake_http(meta, wwan_ip):
                captured["meta"] = meta
                return SendResult(acked=True, should_queue=False)

            with patch(f"{MOD}.get_modem_index", return_value="0"), \
                 patch(f"{MOD}.get_signal_quality", return_value=80), \
                 patch(f"{MOD}.enable_modem", return_value=True), \
                 patch(f"{MOD}.connect_lte", return_value=True), \
                 patch(f"{MOD}.get_bearer_info", return_value=self._bearer()), \
                 patch(f"{MOD}.setup_network_interface", return_value=True), \
                 patch(f"{MOD}.load_local_queue", return_value=[]), \
                 patch(f"{MOD}.send_event_http", side_effect=fake_http), \
                 patch(f"{MOD}.disconnect_lte"), \
                 patch(f"{MOD}.disable_modem"), \
                 patch(f"{MOD}.teardown_network_interface"):
                send_event_with_lte(self.image_path, detection_type="bear", confidence=0.9)

            self.assertIn("event_id", captured["meta"])
            self.assertEqual(captured["meta"]["device_id"], DEVICE_ID)
            self.assertEqual(captured["meta"]["detection_type"], "bear")
            self.assertEqual(captured["meta"]["event_type"], "detection")
            self.assertIn("occurred_at", captured["meta"])

        def test_full_flow_temp_error_saves_queue(self) -> None:
            """一時エラー → キュー保存 → False を返す"""
            with patch(f"{MOD}.get_modem_index", return_value="0"), \
                 patch(f"{MOD}.get_signal_quality", return_value=80), \
                 patch(f"{MOD}.enable_modem", return_value=True), \
                 patch(f"{MOD}.connect_lte", return_value=True), \
                 patch(f"{MOD}.get_bearer_info", return_value=self._bearer()), \
                 patch(f"{MOD}.setup_network_interface", return_value=True), \
                 patch(f"{MOD}.load_local_queue", return_value=[]), \
                 patch(f"{MOD}.send_event_http", return_value=self._fail_result()), \
                 patch(f"{MOD}.save_to_local_queue", return_value=True) as mock_save, \
                 patch(f"{MOD}.disconnect_lte"), \
                 patch(f"{MOD}.disable_modem"), \
                 patch(f"{MOD}.teardown_network_interface"):
                self.assertFalse(send_event_with_lte(self.image_path))
                mock_save.assert_called_once()

        def test_full_flow_permanent_error_no_queue(self) -> None:
            """永続エラー(4xx) → キュー保存しない → False を返す"""
            with patch(f"{MOD}.get_modem_index", return_value="0"), \
                 patch(f"{MOD}.get_signal_quality", return_value=80), \
                 patch(f"{MOD}.enable_modem", return_value=True), \
                 patch(f"{MOD}.connect_lte", return_value=True), \
                 patch(f"{MOD}.get_bearer_info", return_value=self._bearer()), \
                 patch(f"{MOD}.setup_network_interface", return_value=True), \
                 patch(f"{MOD}.load_local_queue", return_value=[]), \
                 patch(f"{MOD}.send_event_http", return_value=self._perm_err_result()), \
                 patch(f"{MOD}.save_to_local_queue", return_value=True) as mock_save, \
                 patch(f"{MOD}.disconnect_lte"), \
                 patch(f"{MOD}.disable_modem"), \
                 patch(f"{MOD}.teardown_network_interface"):
                self.assertFalse(send_event_with_lte(self.image_path))
                mock_save.assert_not_called()

        def test_full_flow_low_signal_saves_queue(self) -> None:
            """電波強度 30%（1–40% 範囲）でスキップ → キュー保存"""
            with patch(f"{MOD}.get_modem_index", return_value="0"), \
                 patch(f"{MOD}.get_signal_quality", return_value=30), \
                 patch(f"{MOD}.save_to_local_queue", return_value=True) as mock_save, \
                 patch(f"{MOD}.teardown_network_interface"):
                self.assertFalse(send_event_with_lte(self.image_path))
                mock_save.assert_called_once()

        def test_full_flow_zero_signal_does_not_skip(self) -> None:
            """電波強度 0%（不明値）はスキップしない → enable まで進む"""
            with patch(f"{MOD}.get_modem_index", return_value="0"), \
                 patch(f"{MOD}.get_signal_quality", return_value=0), \
                 patch(f"{MOD}.enable_modem", return_value=False), \
                 patch(f"{MOD}.save_to_local_queue", return_value=True) as mock_save, \
                 patch(f"{MOD}.teardown_network_interface"), \
                 patch(f"{MOD}.disconnect_lte"), \
                 patch(f"{MOD}.disable_modem"):
                self.assertFalse(send_event_with_lte(self.image_path))
                mock_save.assert_called_once()

        def test_full_flow_none_signal_does_not_skip(self) -> None:
            """電波強度が取得できない場合（None）はスキップしない"""
            with patch(f"{MOD}.get_modem_index", return_value="0"), \
                 patch(f"{MOD}.get_signal_quality", return_value=None), \
                 patch(f"{MOD}.enable_modem", return_value=True), \
                 patch(f"{MOD}.connect_lte", return_value=True), \
                 patch(f"{MOD}.get_bearer_info", return_value=self._bearer()), \
                 patch(f"{MOD}.setup_network_interface", return_value=True), \
                 patch(f"{MOD}.load_local_queue", return_value=[]), \
                 patch(f"{MOD}.send_event_http", return_value=self._ok_result()), \
                 patch(f"{MOD}.disconnect_lte"), \
                 patch(f"{MOD}.disable_modem"), \
                 patch(f"{MOD}.teardown_network_interface"):
                self.assertTrue(send_event_with_lte(self.image_path))

        def test_full_flow_no_modem_saves_queue(self) -> None:
            with patch(f"{MOD}.get_modem_index", return_value=None), \
                 patch(f"{MOD}.save_to_local_queue", return_value=True) as mock_save, \
                 patch(f"{MOD}.teardown_network_interface"):
                self.assertFalse(send_event_with_lte(self.image_path))
                mock_save.assert_called_once()

        def test_full_flow_enable_fail_saves_queue(self) -> None:
            with patch(f"{MOD}.get_modem_index", return_value="0"), \
                 patch(f"{MOD}.get_signal_quality", return_value=80), \
                 patch(f"{MOD}.enable_modem", return_value=False), \
                 patch(f"{MOD}.save_to_local_queue", return_value=True) as mock_save, \
                 patch(f"{MOD}.teardown_network_interface"), \
                 patch(f"{MOD}.disconnect_lte"), \
                 patch(f"{MOD}.disable_modem"):
                self.assertFalse(send_event_with_lte(self.image_path))
                mock_save.assert_called_once()

        def test_full_flow_connect_fail_saves_queue(self) -> None:
            with patch(f"{MOD}.get_modem_index", return_value="0"), \
                 patch(f"{MOD}.get_signal_quality", return_value=80), \
                 patch(f"{MOD}.enable_modem", return_value=True), \
                 patch(f"{MOD}.connect_lte", return_value=False), \
                 patch(f"{MOD}.save_to_local_queue", return_value=True) as mock_save, \
                 patch(f"{MOD}.teardown_network_interface"), \
                 patch(f"{MOD}.disconnect_lte"), \
                 patch(f"{MOD}.disable_modem"):
                self.assertFalse(send_event_with_lte(self.image_path))
                mock_save.assert_called_once()

        def test_full_flow_exception_does_not_crash(self) -> None:
            with patch(f"{MOD}.get_modem_index", side_effect=RuntimeError("unexpected")), \
                 patch(f"{MOD}.save_to_local_queue", return_value=True), \
                 patch(f"{MOD}.teardown_network_interface"):
                self.assertFalse(send_event_with_lte(self.image_path))

        def test_full_flow_finally_always_runs_teardown(self) -> None:
            """connect 失敗でも teardown_network_interface が呼ばれる"""
            with patch(f"{MOD}.get_modem_index", return_value="0"), \
                 patch(f"{MOD}.get_signal_quality", return_value=80), \
                 patch(f"{MOD}.enable_modem", return_value=True), \
                 patch(f"{MOD}.connect_lte", return_value=False), \
                 patch(f"{MOD}.save_to_local_queue", return_value=True), \
                 patch(f"{MOD}.disconnect_lte"), \
                 patch(f"{MOD}.disable_modem"), \
                 patch(f"{MOD}.teardown_network_interface") as mock_teardown:
                send_event_with_lte(self.image_path)
                mock_teardown.assert_called_once()

        # ── inference_pause / inference_resume フック ────────

        def test_inference_pause_called_before_enable(self) -> None:
            """inference_pause が enable_modem より前に呼ばれる"""
            call_order = []
            pause_fn = lambda: call_order.append("pause")

            def mock_enable(idx: str) -> bool:
                call_order.append("enable")
                return False  # 以降の処理を止めるため失敗にする

            with patch(f"{MOD}.get_modem_index", return_value="0"), \
                 patch(f"{MOD}.get_signal_quality", return_value=80), \
                 patch(f"{MOD}.enable_modem", side_effect=mock_enable), \
                 patch(f"{MOD}.save_to_local_queue", return_value=True), \
                 patch(f"{MOD}.teardown_network_interface"), \
                 patch(f"{MOD}.disconnect_lte"), \
                 patch(f"{MOD}.disable_modem"):
                send_event_with_lte(self.image_path, inference_pause=pause_fn)

            self.assertEqual(call_order, ["pause", "enable"])

        def test_inference_resume_called_in_finally_on_success(self) -> None:
            """正常系でも inference_resume が finally で呼ばれる"""
            resume_mock = MagicMock()
            with patch(f"{MOD}.get_modem_index", return_value="0"), \
                 patch(f"{MOD}.get_signal_quality", return_value=80), \
                 patch(f"{MOD}.enable_modem", return_value=True), \
                 patch(f"{MOD}.connect_lte", return_value=True), \
                 patch(f"{MOD}.get_bearer_info", return_value=self._bearer()), \
                 patch(f"{MOD}.setup_network_interface", return_value=True), \
                 patch(f"{MOD}.load_local_queue", return_value=[]), \
                 patch(f"{MOD}.send_event_http", return_value=self._ok_result()), \
                 patch(f"{MOD}.disconnect_lte"), \
                 patch(f"{MOD}.disable_modem"), \
                 patch(f"{MOD}.teardown_network_interface"):
                send_event_with_lte(self.image_path, inference_resume=resume_mock)
            resume_mock.assert_called_once()

        def test_inference_resume_called_even_on_connect_failure(self) -> None:
            """LTE 接続失敗時でも inference_resume が finally で呼ばれる"""
            resume_mock = MagicMock()
            with patch(f"{MOD}.get_modem_index", return_value="0"), \
                 patch(f"{MOD}.get_signal_quality", return_value=80), \
                 patch(f"{MOD}.enable_modem", return_value=True), \
                 patch(f"{MOD}.connect_lte", return_value=False), \
                 patch(f"{MOD}.save_to_local_queue", return_value=True), \
                 patch(f"{MOD}.disconnect_lte"), \
                 patch(f"{MOD}.disable_modem"), \
                 patch(f"{MOD}.teardown_network_interface"):
                send_event_with_lte(self.image_path, inference_resume=resume_mock)
            resume_mock.assert_called_once()

        def test_inference_resume_called_even_on_exception(self) -> None:
            """予期しない例外発生時でも inference_resume が finally で呼ばれる"""
            resume_mock = MagicMock()
            with patch(f"{MOD}.get_modem_index", side_effect=RuntimeError("boom")), \
                 patch(f"{MOD}.save_to_local_queue", return_value=True), \
                 patch(f"{MOD}.teardown_network_interface"):
                send_event_with_lte(self.image_path, inference_resume=resume_mock)
            resume_mock.assert_called_once()

        def test_inference_pause_not_called_on_signal_skip(self) -> None:
            """電波強度スキップ時は inference_pause を呼ばない"""
            pause_mock = MagicMock()
            with patch(f"{MOD}.get_modem_index", return_value="0"), \
                 patch(f"{MOD}.get_signal_quality", return_value=20), \
                 patch(f"{MOD}.save_to_local_queue", return_value=True), \
                 patch(f"{MOD}.teardown_network_interface"):
                send_event_with_lte(self.image_path, inference_pause=pause_mock)
            pause_mock.assert_not_called()

    suite = unittest.TestLoader().loadTestsFromTestCase(TestLteSender)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)


# ===========================================================
# main: 使用例
# ===========================================================

def main() -> None:
    """
    使用例:
        sudo python lte_sender.py           # ダミー画像で LTE 送信テスト
        python lte_sender.py --test         # 単体テスト実行（LTE 不要）

    メイン推論ループからの呼び出し例:
        from lte_sender import send_event_with_lte

        success = send_event_with_lte(
            image_path="/tmp/captured.jpg",
            event_type="detection",
            detection_type="bear",
            confidence=0.92,
            inference_pause=pause_inference,
            inference_resume=resume_inference,
        )
        # success=False でもループは継続（失敗分はキューへ自動保存）
    """
    if "--test" in sys.argv:
        _run_tests()
        return

    # ダミー画像作成（実際はカメラキャプチャ画像を指定）
    image_file = "/tmp/lte_test_event.jpg"
    if not Path(image_file).exists():
        with open(image_file, "wb") as f:
            f.write(b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x00" * 512)
        print(f"ダミー画像作成: {image_file}")

    print("LTE 送信開始...")
    success = send_event_with_lte(
        image_path=image_file,
        event_type="detection",
        detection_type="bear",
        confidence=0.87,
    )
    status = "送信成功" if success else "送信失敗（ローカルキュー保存済み）"
    print(f"結果: {status}")


if __name__ == "__main__":
    main()
