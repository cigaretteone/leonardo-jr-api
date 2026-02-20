#!/usr/bin/env python3
"""
/opt/leonardo/scripts/init_device.py

Leonardo Jr. デバイス初期化スクリプト
systemd サービス: leonardo-init.service

処理:
  1. device_id 生成（未生成の場合のみ）
  2. QR コード生成（未生成の場合のみ）
  3. device_id と QR パスをジャーナルログに出力

設計上の注意:
  - サーバへの pre-register は行わない。
  - devices レコードはユーザーが QR 登録した時点でサーバ側に作成される。
  - これにより起動時の LTE 通信を削減し、圏外での初回起動に対応する。
  - QR 生成失敗はエラーとして記録するが、他の機能（AI検知・ログ保存）の継続を妨げない。
"""

import logging
import sys
from pathlib import Path

# systemd ジャーナル対応のロギング設定
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("leonardo-init")

# スクリプトのディレクトリを sys.path に追加（同ディレクトリのモジュールを import するため）
_SCRIPT_DIR = Path(__file__).parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))


def main() -> int:
    """
    デバイス初期化を実行する。

    Returns:
        終了コード（0: 成功, 1: 致命的エラー）
    """
    logger.info("Leonardo Jr. デバイス初期化を開始します")

    # --- Step 1: device_id 生成 ---
    try:
        from generate_device_id import (
            DEFAULT_DEVICE_ID_PATH,
            generate_device_id,
        )

        device_id_path = DEFAULT_DEVICE_ID_PATH

        if device_id_path.exists():
            device_id = device_id_path.read_text().strip()
            logger.info("device_id は既に存在します: %s", device_id)
        else:
            device_id = generate_device_id()
            logger.info("device_id を生成しました: %s", device_id)

    except Exception as e:
        # device_id 生成失敗は致命的エラー（以降の処理が不可能）
        logger.error("device_id 生成に失敗しました: %s", e)
        return 1

    # --- Step 2: QR コード生成 ---
    try:
        from generate_qr import DEFAULT_QR_PATH, generate_qr

        qr_path = DEFAULT_QR_PATH

        if qr_path.exists():
            logger.info("QR コードは既に存在します: %s", qr_path)
        else:
            url = generate_qr(device_id, qr_path, print_ascii=True)
            logger.info("QR コードを生成しました: %s", qr_path)
            logger.info("セットアップ URL: %s", url)

    except ImportError as e:
        # qrcode ライブラリ未インストールの場合は警告のみ
        logger.warning(
            "QR コード生成スキップ（ライブラリ未インストール）: %s\n"
            "  → pip install qrcode[pil] で解決できます",
            e,
        )
    except Exception as e:
        # QR 生成失敗はエラー記録のみ（デバイスの他機能は継続する）
        logger.error("QR コード生成に失敗しました（他機能は継続します）: %s", e)

    logger.info(
        "初期化完了 — device_id=%s, QR=%s",
        device_id,
        DEFAULT_QR_PATH if "DEFAULT_QR_PATH" in dir() else "N/A",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
