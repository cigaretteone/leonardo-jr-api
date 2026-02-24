#!/usr/bin/env python3
"""
/opt/leonardo/scripts/generate_qr.py

QRコード生成スクリプト

処理:
  1. device_id 読み込み（/etc/leonardo/device_id）
  2. factory_token 生成  → デバイス内部保持のみ、外部に出さない
  3. factory_token_hash 生成 → QRに埋め込む値
  4. URL組み立て:
       https://leonardo-jr-api.onrender.com/setup?device_id={device_id}&fth={factory_token_hash}
     ※ factory_token 自体は URL に含めない（ブラウザ履歴・リファラ・サーバログに残るため）
  5. QRコード画像を /etc/leonardo/qr_setup.png に保存
  6. コンソールに ASCII QR を出力（開発・現場確認用）

依存: pip install qrcode[pil]

セキュリティ注意:
  - FACTORY_SECRET は実証機用固定値。量産機ではワンタイムチャレンジ方式に移行（v1.2）。
  - factory_token は derive して即使用し、ファイルに保存しない。
  - サーバ側には factory_token_hash を保存し、QR の fth パラメータと照合する。
"""

import hashlib
import os
import sys
from pathlib import Path

try:
    import qrcode
except ImportError:
    print(
        "Error: qrcode ライブラリが見つかりません。以下を実行してください:\n"
        "  pip install qrcode[pil]",
        file=sys.stderr,
    )
    sys.exit(1)

from generate_device_id import DEFAULT_DEVICE_ID_PATH

# QR画像の保存先（デフォルト）
DEFAULT_QR_PATH = Path("/etc/leonardo/qr_setup.png")

# セットアップ画面のベースURL
SETUP_BASE_URL = "https://leonardo-jr-api.onrender.com/setup"

def derive_factory_token(device_id: str) -> str:
    """
    device_id と環境変数 FACTORY_SECRET から factory_token を導出する。

    この値はデバイス内部でのみ使用し、外部（URL・ログ等）には出さない。
    サーバ側も同じ計算式で factory_token を再導出し、そのハッシュと照合する。

    Raises:
        KeyError: 環境変数 FACTORY_SECRET が未設定の場合
    """
    secret = os.environ["FACTORY_SECRET"]
    raw = f"{device_id}:{secret}".encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def derive_factory_token_hash(factory_token: str) -> str:
    """
    factory_token をさらにハッシュ化して factory_token_hash を導出する。

    この値のみ QR コードの URL パラメータ (fth) として公開する。
    サーバ側では保存済みの factory_token_hash と QR の fth を比較照合する。
    """
    return hashlib.sha256(factory_token.encode()).hexdigest()[:16]


def build_setup_url(device_id: str) -> str:
    """
    QR コードに埋め込むセットアップ URL を組み立てる。

    URL には factory_token_hash (fth) のみ含む。
    factory_token（平文）は URL に含めない。
    """
    factory_token = derive_factory_token(device_id)
    fth = derive_factory_token_hash(factory_token)
    return f"{SETUP_BASE_URL}?device_id={device_id}&fth={fth}"


def generate_qr(
    device_id: str,
    output_path: Path = DEFAULT_QR_PATH,
    print_ascii: bool = True,
) -> str:
    """
    QR コードを生成して PNG に保存する。

    Args:
        device_id:   対象デバイスの device_id
        output_path: QR 画像の保存先（デフォルト: /etc/leonardo/qr_setup.png）
        print_ascii: True の場合、コンソールに ASCII QR を出力する

    Returns:
        セットアップ URL 文字列
    """
    url = build_setup_url(device_id)

    # QR コードオブジェクト生成
    qr = qrcode.QRCode(
        version=None,  # データ量に応じて自動サイズ決定
        error_correction=qrcode.constants.ERROR_CORRECT_M,  # ~15% 誤り訂正
        box_size=10,
        border=4,
    )
    qr.add_data(url)
    qr.make(fit=True)

    # PNG 保存
    output_path.parent.mkdir(parents=True, exist_ok=True)
    img = qr.make_image(fill_color="black", back_color="white")
    img.save(str(output_path))

    # ASCII QR 出力（開発・現場確認用）
    if print_ascii:
        print("\n--- ASCII QR (開発確認用) ---")
        qr.print_ascii(invert=True)
        print(f"\nSetup URL : {url}")
        print(f"QR 保存先 : {output_path}")

    return url


def main() -> None:
    device_id_path = DEFAULT_DEVICE_ID_PATH

    if not device_id_path.exists():
        print(
            "Error: device_id が見つかりません。先に generate_device_id.py を実行してください。",
            file=sys.stderr,
        )
        sys.exit(1)

    device_id = device_id_path.read_text().strip()
    generate_qr(device_id)


if __name__ == "__main__":
    main()
