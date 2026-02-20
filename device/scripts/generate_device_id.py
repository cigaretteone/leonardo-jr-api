#!/usr/bin/env python3
"""
/opt/leonardo/scripts/generate_device_id.py

device_id 生成スクリプト

処理:
  1. /etc/leonardo/device_id が存在すれば読み込んで返す（冪等）
  2. 存在しなければ以下で生成:
       - CPUシリアル取得: /proc/cpuinfo の Serial 行の下8桁
       - 4桁英数字乱数生成
       - device_id = f"LJ-{cpu_serial_last8}-{random4}"
       - /etc/leonardo/device_id に保存
  3. device_id を標準出力に返す

依存: 標準ライブラリのみ
"""

import secrets
import string
import sys
from pathlib import Path

# デフォルトパス（テスト時に上書き可能）
DEFAULT_DEVICE_ID_PATH = Path("/etc/leonardo/device_id")
DEFAULT_CPUINFO_PATH = Path("/proc/cpuinfo")


def get_cpu_serial(cpuinfo_path: Path = DEFAULT_CPUINFO_PATH) -> str:
    """
    Raspberry Pi の CPUシリアルを取得し、下8桁を返す（大文字）。

    Raspberry Pi 以外の環境（開発PC等）では "DEVDUMMY" を返す。
    量産機では固有シリアルが確実に取得できるため、フォールバックは
    実証機デモ用途でのみ使用される。
    """
    try:
        with open(cpuinfo_path, "r") as f:
            for line in f:
                if line.startswith("Serial"):
                    serial = line.split(":")[1].strip()
                    if len(serial) >= 8:
                        return serial[-8:].upper()
    except (FileNotFoundError, PermissionError, IndexError):
        pass

    # Raspberry Pi 以外の環境ではダミー値を使用
    # 同一ネット内の複数台衝突を避けるため、4桁ランダムサフィックスを付与する
    print(
        "Warning: CPUシリアル取得不可。開発環境用ダミー値を使用します。",
        file=sys.stderr,
    )
    charset = string.ascii_uppercase + string.digits
    return "DEVDUMMY" + "".join(secrets.choice(charset) for _ in range(4))


def generate_random_4() -> str:
    """4桁の英大文字+数字の乱数文字列を生成する（暗号論的乱数）。"""
    charset = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(charset) for _ in range(4))


def generate_device_id(
    device_id_path: Path = DEFAULT_DEVICE_ID_PATH,
    cpuinfo_path: Path = DEFAULT_CPUINFO_PATH,
) -> str:
    """
    device_id を生成して保存し、返す。

    既に device_id_path にファイルが存在する場合はそれを返す（冪等）。
    新規生成時は CPUシリアル下8桁 + 4桁乱数で構成する。

    Args:
        device_id_path: device_id 保存先パス（デフォルト: /etc/leonardo/device_id）
        cpuinfo_path:   CPUシリアル取得元（デフォルト: /proc/cpuinfo）

    Returns:
        device_id 文字列 (例: "LJ-A3F8B2C1-7294")
    """
    # 既存の device_id があれば再利用（冪等性確保）
    if device_id_path.exists():
        device_id = device_id_path.read_text().strip()
        return device_id

    # 新規生成
    cpu_serial_last8 = get_cpu_serial(cpuinfo_path)
    random_4 = generate_random_4()
    device_id = f"LJ-{cpu_serial_last8}-{random_4}"

    # 保存先ディレクトリを作成（/etc/leonardo/ がなければ）
    device_id_path.parent.mkdir(parents=True, exist_ok=True)
    device_id_path.write_text(device_id)

    return device_id


def main() -> None:
    device_id = generate_device_id()
    print(device_id)


if __name__ == "__main__":
    main()
