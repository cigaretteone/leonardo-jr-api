#!/usr/bin/env python3
"""
lte_sender.py — Leonardo Jr. LTE送信モジュール
野外設置型省電力エッジAIデバイス用 イベント駆動型LTE送信

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

  send_event_http(image_path, metadata) -> bool
      HTTP POST で画像 + メタデータを送信する（指数バックオフ再試行）。

  save_to_local_queue(image_path, metadata) -> bool
      送信失敗イベントをローカルキュー (JSON ファイル) に保存する。

  load_local_queue() -> list[Path]
      ローカルキューの JSON ファイルを古い順で返す。

  process_local_queue(limit) -> int
      LTE 接続済み状態で未送信キューを再送し、成功件数を返す（上限 limit 件）。

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
        └─ 失敗 → ローカルキュー保存
  [10] finally: simple-disconnect → disable → ネットワーク削除 → 推論再開フック（常に実行）
"""

import json
import logging
import subprocess
import sys
import time
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
ENDPOINT_URL = "https://leonardo-receiver.onrender.com/api/v1/events"  # 送信先エンドポイント（要差し替え）
BEARER_TOKEN = "leo-jr-secret-2024"             # Bearer 認証トークン（要差し替え）
DEVICE_ID = "leonardo_jr_001"

APN = "ppsim.jp"
APN_USER = "pp@sim"
APN_PASSWORD = "jpn"
NETWORK_INTERFACE = "wwan0"

LTE_QUEUE_DIR = Path("/home/manta/leonardo_jr/lte_queue")
LTE_QUEUE_MAX = 100

CONNECT_TIMEOUT_SEC = 30    # LTE 接続タイムアウト（秒）
SEND_TIMEOUT_SEC = 60       # HTTP 送信タイムアウト（秒）
MAX_RETRY = 3               # HTTP 送信最大再試行回数
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

# ===========================================================
# [Dual Reporting] New API Config (Alpha)
# ===========================================================
JR_API_URL_TEMPLATE = "https://leonardo-jr-api.onrender.com/api/v1/devices/{}/event"
JR_API_TOKEN = "m0lCjXhKXBooGZ87ty_ASxbIQh0iD_MQwrYC-CVYuNU"
JR_DEVICE_ID = "LJ-671493E4-QDSF"

# テストで patch するためのモジュール名（直接実行時は "__main__"）
_MODULE = __name__


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
# HTTP 送信関数
# ===========================================================

def send_event_http(image_path: str, metadata: dict, wwan_ip: str) -> bool:
    # Use SourceIPAdapter to force LTE interface
    session = requests.Session()
    session.mount("https://", SourceIPAdapter(wwan_ip))
    session.mount("http://", SourceIPAdapter(wwan_ip))
    # Resolve DNS via 8.8.8.8 to bypass Tailscale
    from urllib.parse import urlparse
    _parsed = urlparse(ENDPOINT_URL)
    _resolved = _resolve_host(_parsed.hostname)
    logger.info("DNS resolved %s -> %s", _parsed.hostname, _resolved)
    def _patched_gai(host, port, family=0, type=0, proto=0, flags=0):
        if host == _parsed.hostname:
            host = _resolved
        return _orig_getaddrinfo(host, port, family, type, proto, flags)
    socket.getaddrinfo = _patched_gai
    
    try:
        with open(image_path, "rb") as f:
            files = {"image": (Path(image_path).name, f, "image/jpeg")}
            resp = session.post(
                ENDPOINT_URL, 
                files=files, 
                data={"metadata": __import__("json").dumps(metadata)}, 
                headers={"Authorization": f"Bearer {BEARER_TOKEN}"},
                timeout=SEND_TIMEOUT_SEC
            )
            socket.getaddrinfo = _orig_getaddrinfo
        return resp.status_code == 200
    except Exception as e:
        socket.getaddrinfo = _orig_getaddrinfo
        logger.error(f"HTTP upload failed: {e}")
        return False


# ===========================================================
# ローカルキュー管理関数
# ===========================================================


def send_to_jr_api(metadata: dict, wwan_ip: str) -> None:
    """
    [Dual Reporting] Send event metadata to the new Leonardo Jr. API.
    Does not block or fail the main process on error.
    """
    try:
        import requests
        import socket
        from urllib.parse import urlparse
        
        device_id = JR_DEVICE_ID
        url = JR_API_URL_TEMPLATE.format(device_id)
        
        raw_type = metadata.get("event_type", "wildlife_detected")
        det_type = "bear" if "wildlife" in raw_type or "bear" in raw_type else "other"
        
        payload = {
            "detection_type": det_type,
            "confidence": metadata.get("confidence", 0.0),
            "timestamp": metadata.get("timestamp")
        }
        
        logger.info(f"JR_API: Sending dual report to {url}")

        session = requests.Session()
        session.mount("https://", SourceIPAdapter(wwan_ip))
        
        try:
            _parsed = urlparse(url)
            if '_resolve_host' in globals() and '_orig_getaddrinfo' in globals():
                _resolved = _resolve_host(_parsed.hostname)
                def _patched_gai(host, port, family=0, type=0, proto=0, flags=0):
                    if host == _parsed.hostname:
                        host = _resolved
                    return _orig_getaddrinfo(host, port, family, type, proto, flags)
                socket.getaddrinfo = _patched_gai
        except Exception:
            pass 

        try:
            resp = session.post(
                url,
                json=payload,
                headers={
                    "X-Api-Token": JR_API_TOKEN,
                    "Content-Type": "application/json"
                },
                timeout=10
            )
            if resp.status_code in [200, 201]:
                logger.info(f"JR_API: Success (ID: {resp.json().get('event_id')})")
            else:
                logger.error(f"JR_API: Failed {resp.status_code} {resp.text}")
        finally:
             if '_orig_getaddrinfo' in globals():
                 socket.getaddrinfo = _orig_getaddrinfo

    except Exception as e:
        logger.error(f"JR_API: Exception {e}")
        if '_orig_getaddrinfo' in globals():
             socket.getaddrinfo = _orig_getaddrinfo
def save_to_local_queue(image_path: str, metadata: dict) -> bool:
    """
    送信失敗イベントをローカルキュー（JSON ファイル）に保存する。
    ファイル名: {queued_at_usec}_{event_timestamp}.json
    上限（LTE_QUEUE_MAX 件）を超えた場合、最古のファイルを削除する。
    Returns: 保存成功時 True
    """
    try:
        LTE_QUEUE_DIR.mkdir(parents=True, exist_ok=True)

        # マイクロ秒精度のタイムスタンプでファイル名衝突を回避
        queued_ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        event_id = (
            metadata.get("timestamp", queued_ts)
            .replace(":", "")
            .replace("+", "p")
            .replace(".", "")
            [:20]
        )
        filename = f"{queued_ts}_{event_id}.json"
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
    送信成功したファイルは削除。画像が消えたエントリも削除（再送不能）。
    破損した JSON ファイルは削除してスキップする。
    limit を超えた残りキューは次回セッションに持ち越す。
    # 1件あたり: 平均300mA × 10秒 ≈ 0.83mAh（limit=3 で最大 ≈ 2.49mAh）
    Args:
        limit : 1回のセッションで再送する最大件数（デフォルト 3）
    Returns: 送信成功件数
    """
    queue_files = load_local_queue()
    total = len(queue_files)
    target = queue_files[:limit]
    sent_count = 0

    for qfile in target:
        try:
            entry = json.loads(qfile.read_text(encoding="utf-8"))
            image_path = entry.get("image_path", "")
            meta = entry.get("metadata", {})
        except (json.JSONDecodeError, OSError):
            qfile.unlink(missing_ok=True)
            continue

        if not Path(image_path).exists():
            qfile.unlink(missing_ok=True)
            continue

        if send_event_http(image_path, meta, wwan_ip):
            qfile.unlink(missing_ok=True)
            sent_count += 1

    remaining = total - limit
    if remaining > 0:
        logger.warning("キュー残り %d 件（次回持ち越し）", remaining)

    return sent_count


# ===========================================================
# メインエントリポイント
# ===========================================================

def send_event_with_lte(
    image_path: str,
    event_type: str = "wildlife_detected",
    confidence: float = 0.0,
    timestamp: Optional[str] = None,
    inference_pause: Optional[Callable[[], None]] = None,
    inference_resume: Optional[Callable[[], None]] = None,
) -> bool:
    """
    検知イベント発生時の LTE 送信エントリポイント。
    いかなる例外が発生してもメインの推論ループを止めない。

    === 消費電力内訳（1 イベント、キュー 0 件の場合）===
    # [1] モデム検出 (mmcli -L):         約 50mA ×  1秒 ≈ 0.01mAh
    # [2] 電波強度確認 (mmcli -m):       約 50mA ×  1秒 ≈ 0.01mAh
    # [3] enable (mmcli --enable):        約200mA ×  5秒 ≈ 0.28mAh
    # [4] connect (simple-connect):       約300mA × 15秒 ≈ 1.25mAh
    # [5] Bearer 取得・ネットワーク設定: 約 50mA ×  3秒 ≈ 0.04mAh
    # [6] HTTP POST (50KB 画像):          約300mA × 10秒 ≈ 0.83mAh
    # [7] disconnect (simple-disconnect): 約200mA ×  3秒 ≈ 0.17mAh
    # [8] disable (mmcli --disable):      約150mA ×  3秒 ≈ 0.13mAh
    # ──────────────────────────────────────────────────────────
    # 合計（キュー 0 件）:                              ≈ 2.72mAh
    # ← 制約 6mAh 以内（キュー limit=3 件追加でも ≈ 5.21mAh）

    Args:
        image_path       : 送信する JPEG ファイルパス（≤50KB）
        event_type       : イベント種別（デフォルト: "wildlife_detected"）
        confidence       : 検出信頼度 0.0–1.0
        timestamp        : ISO8601 タイムスタンプ（None で自動生成）
        inference_pause  : LTE 通信前に呼ぶ AI 推論停止コールバック（省略可）
        inference_resume : LTE 通信後に呼ぶ AI 推論再開コールバック（省略可）
    Returns:
        送信成功時 True、スキップ / 失敗時 False
    """
    if timestamp is None:
        timestamp = datetime.now(timezone.utc).isoformat()

    metadata: dict = {
        "timestamp": timestamp,
        "device_id": DEVICE_ID,
        "event_type": event_type,
        "confidence": round(float(confidence), 4),
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
        # # 電波強度チェック: 約50mA × 1秒 ≈ 0.01mAh
        # 0% は disable 直後の不明値のためスキップ判定から除外する
        signal = get_signal_quality(modem_index)
        if signal is not None and signal > 0 and signal <= SIGNAL_SKIP_THRESHOLD:
            logger.warning(
                "電波強度 %d%% ≤ %d%%: キュー保存してスキップ",
                signal, SIGNAL_SKIP_THRESHOLD,
            )
            save_to_local_queue(image_path, metadata)
            return False

        # [3] AI 推論停止フック（LTE 通信中は推論を停止する）
        if inference_pause:
            inference_pause()

        # [4] モデム enable
        # # enable → connect サイクル: 平均300mA × 20秒 ≈ 1.67mAh
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
        # # キュー再送: 平均300mA × 10秒 × 件数 ≈ 0.83mAh/件
        pending = load_local_queue()
        if pending:
            sent = process_local_queue(wwan_ip=bearer["ip"])
            logger.info("キュー再送: %d 件成功（キュー残: %d 件）", sent, len(pending) - sent)

        # [8] 本イベント送信
        # # HTTP POST: 平均300mA × 10秒 ≈ 0.83mAh
        success = send_event_http(image_path, metadata, bearer["ip"])

        # [8b] Dual Report to New API (Fire and Forget)
        send_to_jr_api(metadata, bearer["ip"])
        if success:
            logger.info("送信成功: %s (confidence=%.2f)", event_type, confidence)
        else:
            logger.error("送信失敗: キュー保存")
            save_to_local_queue(image_path, metadata)

        return success

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
        # # disconnect + disable + teardown: 平均175mA × 8秒 ≈ 0.39mAh
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
            meta = {"timestamp": "2024-01-01T00:00:00+00:00", "device_id": "test"}
            with patch(f"{MOD}.LTE_QUEUE_DIR", test_queue):
                self.assertTrue(save_to_local_queue(self.image_path, meta))
                files = load_local_queue()
                self.assertEqual(len(files), 1)
                entry = json.loads(files[0].read_text())
                self.assertEqual(entry["image_path"], self.image_path)
                self.assertEqual(entry["metadata"]["timestamp"], meta["timestamp"])

        def test_queue_max_limit_deletes_oldest(self) -> None:
            test_queue = self.tmpdir / "queue"
            meta = {"timestamp": "2024-01-01T00:00:00+00:00"}
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
            meta = {"timestamp": "2024-01-01T00:00:00+00:00"}
            with patch(f"{MOD}.LTE_QUEUE_DIR", test_queue):
                for _ in range(count):
                    time.sleep(0.005)
                    save_to_local_queue(self.image_path, meta)
            return test_queue

        def test_process_local_queue_respects_limit(self) -> None:
            """limit=2 のとき 2 件しか処理しない"""
            test_queue = self._make_queue_files(5)
            with patch(f"{MOD}.LTE_QUEUE_DIR", test_queue), \
                 patch(f"{MOD}.send_event_http", return_value=True):
                sent = process_local_queue(limit=2)
            self.assertEqual(sent, 2)
            # 残り 3 件がキューに残っている
            self.assertEqual(len(sorted(test_queue.glob("*.json"))), 3)

        def test_process_local_queue_warns_remaining(self) -> None:
            """limit より多くのキューがある場合に warning を出す"""
            test_queue = self._make_queue_files(5)
            with patch(f"{MOD}.LTE_QUEUE_DIR", test_queue), \
                 patch(f"{MOD}.send_event_http", return_value=True), \
                 self.assertLogs(level="WARNING") as log_ctx:
                process_local_queue(limit=3)
            self.assertTrue(
                any("キュー残り" in msg and "次回持ち越し" in msg for msg in log_ctx.output)
            )

        def test_process_local_queue_no_warning_within_limit(self) -> None:
            """キュー件数が limit 以下なら warning を出さない"""
            test_queue = self._make_queue_files(2)
            with patch(f"{MOD}.LTE_QUEUE_DIR", test_queue), \
                 patch(f"{MOD}.send_event_http", return_value=True):
                import logging as _logging
                with self.assertRaises(AssertionError):
                    with self.assertLogs(level="WARNING"):
                        process_local_queue(limit=3)

        def test_process_local_queue_default_limit_3(self) -> None:
            """デフォルト limit=3 で 3 件を処理する"""
            test_queue = self._make_queue_files(5)
            with patch(f"{MOD}.LTE_QUEUE_DIR", test_queue), \
                 patch(f"{MOD}.send_event_http", return_value=True):
                sent = process_local_queue(wwan_ip=bearer["ip"])  # limit=3 がデフォルト
            self.assertEqual(sent, 3)

        def test_process_local_queue_skips_missing_image(self) -> None:
            """画像が消えたエントリは削除してスキップ"""
            test_queue = self.tmpdir / "queue"
            test_queue.mkdir()
            # 存在しない画像パスのエントリを直接作成
            entry = {
                "queued_at": "2024-01-01T00:00:00+00:00",
                "image_path": "/nonexistent/ghost.jpg",
                "metadata": {},
            }
            (test_queue / "20240101T000000000000_test.json").write_text(
                json.dumps(entry), encoding="utf-8"
            )
            with patch(f"{MOD}.LTE_QUEUE_DIR", test_queue):
                sent = process_local_queue(limit=3)
            self.assertEqual(sent, 0)
            # エントリが削除されている
            self.assertEqual(len(list(test_queue.glob("*.json"))), 0)

        # ── send_event_http ──────────────────────────────────

        def test_http_success_200(self) -> None:
            with patch("requests.post") as m:
                m.return_value = MagicMock(status_code=200)
                self.assertTrue(send_event_http(self.image_path, {"timestamp": "T"}, "127.0.0.1"))

        def test_http_success_201(self) -> None:
            with patch("requests.post") as m:
                m.return_value = MagicMock(status_code=201)
                self.assertTrue(send_event_http(self.image_path, {}, "127.0.0.1"))

        def test_http_4xx_no_retry(self) -> None:
            with patch("requests.post") as m:
                m.return_value = MagicMock(status_code=401)
                self.assertFalse(send_event_http(self.image_path, {}, "127.0.0.1"))
                self.assertEqual(m.call_count, 1)

        def test_http_connection_error_retry_then_success(self) -> None:
            with patch("requests.post") as m, patch("time.sleep"):
                m.side_effect = [
                    requests.exceptions.ConnectionError(),
                    MagicMock(status_code=200),
                ]
                self.assertTrue(send_event_http(self.image_path, {}, "127.0.0.1"))
                self.assertEqual(m.call_count, 2)

        def test_http_timeout_retry_then_success(self) -> None:
            with patch("requests.post") as m, patch("time.sleep"):
                m.side_effect = [
                    requests.exceptions.Timeout(),
                    MagicMock(status_code=200),
                ]
                self.assertTrue(send_event_http(self.image_path, {}, "127.0.0.1"))
                self.assertEqual(m.call_count, 2)

        def test_http_max_retry_exceeded(self) -> None:
            with patch("requests.post") as m, patch("time.sleep"):
                m.side_effect = requests.exceptions.ConnectionError()
                self.assertFalse(send_event_http(self.image_path, {}, "127.0.0.1"))
                self.assertEqual(m.call_count, MAX_RETRY)

        def test_http_missing_image_no_retry(self) -> None:
            self.assertFalse(send_event_http("/nonexistent/path.jpg", {}, "127.0.0.1"))

        # ── send_event_with_lte フルフロー ──────────────────

        def _bearer(self) -> dict:
            return {"ip": "10.0.0.1", "prefix": "28", "gateway": "10.0.0.14"}

        def test_full_flow_success(self) -> None:
            with patch(f"{MOD}.get_modem_index", return_value="0"), \
                 patch(f"{MOD}.get_signal_quality", return_value=80), \
                 patch(f"{MOD}.enable_modem", return_value=True), \
                 patch(f"{MOD}.connect_lte", return_value=True), \
                 patch(f"{MOD}.get_bearer_info", return_value=self._bearer()), \
                 patch(f"{MOD}.setup_network_interface", return_value=True), \
                 patch(f"{MOD}.load_local_queue", return_value=[]), \
                 patch(f"{MOD}.send_event_http", return_value=True), \
                 patch(f"{MOD}.disconnect_lte"), \
                 patch(f"{MOD}.disable_modem"), \
                 patch(f"{MOD}.teardown_network_interface"):
                self.assertTrue(
                    send_event_with_lte(self.image_path, confidence=0.95)
                )

        def test_full_flow_low_signal_saves_queue(self) -> None:
            """電波強度 30%（1–40% 範囲）でスキップ→キュー保存"""
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
                # enable が失敗することでキュー保存されるが、スキップ理由ではない
                self.assertFalse(send_event_with_lte(self.image_path))
                mock_save.assert_called_once()

        def test_full_flow_none_signal_does_not_skip(self) -> None:
            """電波強度が取得できない場合（None）はスキップしない → enable まで進む"""
            with patch(f"{MOD}.get_modem_index", return_value="0"), \
                 patch(f"{MOD}.get_signal_quality", return_value=None), \
                 patch(f"{MOD}.enable_modem", return_value=True), \
                 patch(f"{MOD}.connect_lte", return_value=True), \
                 patch(f"{MOD}.get_bearer_info", return_value=self._bearer()), \
                 patch(f"{MOD}.setup_network_interface", return_value=True), \
                 patch(f"{MOD}.load_local_queue", return_value=[]), \
                 patch(f"{MOD}.send_event_http", return_value=True), \
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

        def test_full_flow_http_fail_saves_queue(self) -> None:
            with patch(f"{MOD}.get_modem_index", return_value="0"), \
                 patch(f"{MOD}.get_signal_quality", return_value=80), \
                 patch(f"{MOD}.enable_modem", return_value=True), \
                 patch(f"{MOD}.connect_lte", return_value=True), \
                 patch(f"{MOD}.get_bearer_info", return_value=self._bearer()), \
                 patch(f"{MOD}.setup_network_interface", return_value=True), \
                 patch(f"{MOD}.load_local_queue", return_value=[]), \
                 patch(f"{MOD}.send_event_http", return_value=False), \
                 patch(f"{MOD}.save_to_local_queue", return_value=True) as mock_save, \
                 patch(f"{MOD}.disconnect_lte"), \
                 patch(f"{MOD}.disable_modem"), \
                 patch(f"{MOD}.teardown_network_interface"):
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
                 patch(f"{MOD}.send_event_http", return_value=True), \
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

        # AI 推論の停止・再開コールバックを渡す例
        def pause_inference():
            # 例: カメラ取得・推論スレッドを一時停止する処理
            pass

        def resume_inference():
            # 例: カメラ取得・推論スレッドを再開する処理
            pass

        success = send_event_with_lte(
            image_path="/tmp/captured.jpg",
            event_type="wildlife_detected",
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
        event_type="wildlife_detected",
        confidence=0.87,
    )
    status = "送信成功" if success else "送信失敗（ローカルキュー保存済み）"
    print(f"結果: {status}")


if __name__ == "__main__":
    main()

