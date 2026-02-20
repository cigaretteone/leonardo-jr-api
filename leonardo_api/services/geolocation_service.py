"""
services/geolocation_service.py — IPジオロケーション + 距離計算

ip-api.com（無料、45req/min）を使って IP の地域・座標を取得し、
登録座標との Haversine 距離を計算する。

設計注意:
  - LTE IP のジオロケーションは数十 km 単位でズレることがある。
  - キャリア NAT で遠隔地域の IP が割り当てられるケースもある。
  - 実証機ではデモ中の誤検知を防ぐため閾値を 150km に設定（設計書 §7.2）。
  - 量産機では MaxMind GeoIP2 ローカル DB に移行予定。
"""

import math
import logging
from dataclasses import dataclass

import httpx

from ..config import settings

logger = logging.getLogger(__name__)

# ip-api.com から取得するフィールド（最小限に絞ってレスポンスを軽量化）
_IP_API_FIELDS = "status,regionName,lat,lon"
_IP_API_TIMEOUT = 5.0  # 秒（発報ハンドラをブロックしすぎないための上限）


@dataclass
class GeolocationResult:
    region: str          # 都道府県 / 地域名（例: "長野県"）
    lat: float           # IPジオロケーションの緯度
    lon: float           # IPジオロケーションの経度
    available: bool      # 取得成功フラグ（False の場合は lat/lon = 0.0）


async def get_geolocation(ip: str) -> GeolocationResult:
    """
    ip-api.com から IP の地域・座標を取得する。

    プライベート IP や取得失敗の場合は available=False を返し、
    呼び出し元で location_mismatch を False に設定すること（誤検知防止）。
    """
    # プライベート IP はジオロケーション不可
    if _is_private_ip(ip):
        logger.debug("プライベート IP のためジオロケーションをスキップ: %s", ip)
        return GeolocationResult(region="", lat=0.0, lon=0.0, available=False)

    url = f"{settings.GEOLOCATION_API_URL}/{ip}?fields={_IP_API_FIELDS}&lang=ja"

    try:
        async with httpx.AsyncClient(timeout=_IP_API_TIMEOUT) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()

        if data.get("status") != "success":
            logger.warning("ジオロケーション失敗 (ip=%s): %s", ip, data)
            return GeolocationResult(region="", lat=0.0, lon=0.0, available=False)

        return GeolocationResult(
            region=data.get("regionName", ""),
            lat=float(data.get("lat", 0.0)),
            lon=float(data.get("lon", 0.0)),
            available=True,
        )

    except (httpx.HTTPError, KeyError, ValueError) as e:
        logger.warning("ジオロケーション API エラー (ip=%s): %s", ip, e)
        return GeolocationResult(region="", lat=0.0, lon=0.0, available=False)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    2点間の Haversine 距離をキロメートルで返す。

    Args:
        lat1, lon1: 地点1の緯度・経度（度）
        lat2, lon2: 地点2の緯度・経度（度）

    Returns:
        距離（km）
    """
    R = 6371.0  # 地球半径（km）
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


async def check_location_mismatch(
    registered_lat: float,
    registered_lon: float,
    registered_region: str,
    event_ip: str,
) -> tuple[bool, float | None, str]:
    """
    発報 IP の位置と登録座標を比較し、逸脱の有無を判定する。

    判定条件（設計書 §7.2）:
      - Haversine 距離 >= 150km、または
      - 都道府県（regionName）が異なる

    Args:
        registered_lat:    登録座標の緯度
        registered_lon:    登録座標の経度
        registered_region: 登録座標の都道府県名（ip ベースではなく GPS 座標から逆ジオコード
                           するのが理想だが、実証機では空文字列で比較をスキップする）
        event_ip:          発報時のデバイス IP

    Returns:
        (location_mismatch: bool, distance_km: float | None, region: str)
    """
    geo = await get_geolocation(event_ip)

    if not geo.available:
        # ジオロケーション不可 → 誤検知を避けるため mismatch = False
        return False, None, ""

    distance_km = haversine_km(registered_lat, registered_lon, geo.lat, geo.lon)

    # 距離閾値判定
    distance_mismatch = distance_km >= settings.LOCATION_MISMATCH_THRESHOLD_KM

    # 都道府県一致判定（登録座標の region が空の場合はスキップ）
    region_mismatch = (
        bool(registered_region)
        and bool(geo.region)
        and registered_region != geo.region
    )

    mismatch = distance_mismatch or region_mismatch
    return mismatch, round(distance_km, 3), geo.region


def _is_private_ip(ip: str) -> bool:
    """プライベート IP アドレス / ループバック / リンクローカルを判定する。"""
    import ipaddress
    try:
        addr = ipaddress.ip_address(ip)
        return addr.is_private or addr.is_loopback or addr.is_link_local
    except ValueError:
        return False
