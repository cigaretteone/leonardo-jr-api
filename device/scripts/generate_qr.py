#!/usr/bin/env python3
"""
/opt/leonardo/scripts/generate_qr.py

QR繧ｳ繝ｼ繝臥函謌舌せ繧ｯ繝ｪ繝励ヨ

蜃ｦ逅・
  1. device_id 隱ｭ縺ｿ霎ｼ縺ｿ・・etc/leonardo/device_id・・
  2. factory_token 逕滓・  竊・繝・ヰ繧､繧ｹ蜀・Κ菫晄戟縺ｮ縺ｿ縲∝､夜Κ縺ｫ蜃ｺ縺輔↑縺・
  3. factory_token_hash 逕滓・ 竊・QR縺ｫ蝓九ａ霎ｼ繧蛟､
  4. URL邨・∩遶九※:
       https://leonardo-jr-api.onrender.com/setup?device_id={device_id}&fth={factory_token_hash}
     窶ｻ factory_token 閾ｪ菴薙・ URL 縺ｫ蜷ｫ繧√↑縺・ｼ医ヶ繝ｩ繧ｦ繧ｶ螻･豁ｴ繝ｻ繝ｪ繝輔ぃ繝ｩ繝ｻ繧ｵ繝ｼ繝舌Ο繧ｰ縺ｫ谿九ｋ縺溘ａ・・
  5. QR繧ｳ繝ｼ繝臥判蜒上ｒ /etc/leonardo/qr_setup.png 縺ｫ菫晏ｭ・
  6. 繧ｳ繝ｳ繧ｽ繝ｼ繝ｫ縺ｫ ASCII QR 繧貞・蜉幢ｼ磯幕逋ｺ繝ｻ迴ｾ蝣ｴ遒ｺ隱咲畑・・

萓晏ｭ・ pip install qrcode[pil]

繧ｻ繧ｭ繝･繝ｪ繝・ぅ豕ｨ諢・
  - FACTORY_SECRET 縺ｯ螳溯ｨｼ讖溽畑蝗ｺ螳壼､縲る㍼逕｣讖溘〒縺ｯ繝ｯ繝ｳ繧ｿ繧､繝繝√Ε繝ｬ繝ｳ繧ｸ譁ｹ蠑上↓遘ｻ陦鯉ｼ・1.2・峨・
  - factory_token 縺ｯ derive 縺励※蜊ｳ菴ｿ逕ｨ縺励√ヵ繧｡繧､繝ｫ縺ｫ菫晏ｭ倥＠縺ｪ縺・・
  - 繧ｵ繝ｼ繝仙・縺ｫ縺ｯ factory_token_hash 繧剃ｿ晏ｭ倥＠縲＿R 縺ｮ fth 繝代Λ繝｡繝ｼ繧ｿ縺ｨ辣ｧ蜷医☆繧九・
"""

import hashlib
import os
import sys
from pathlib import Path

try:
    import qrcode
except ImportError:
    print(
        "Error: qrcode 繝ｩ繧､繝悶Λ繝ｪ縺瑚ｦ九▽縺九ｊ縺ｾ縺帙ｓ縲ゆｻ･荳九ｒ螳溯｡後＠縺ｦ縺上□縺輔＞:\n"
        "  pip install qrcode[pil]",
        file=sys.stderr,
    )
    sys.exit(1)

from generate_device_id import DEFAULT_DEVICE_ID_PATH

# QR逕ｻ蜒上・菫晏ｭ伜・・医ョ繝輔か繝ｫ繝茨ｼ・
DEFAULT_QR_PATH = Path("/etc/leonardo/qr_setup.png")

# 繧ｻ繝・ヨ繧｢繝・・逕ｻ髱｢縺ｮ繝吶・繧ｹURL
SETUP_BASE_URL = "https://leonardo-jr-api.onrender.com/setup"

def derive_factory_token(device_id: str) -> str:
    """
    device_id 縺ｨ迺ｰ蠅・､画焚 FACTORY_SECRET 縺九ｉ factory_token 繧貞ｰ主・縺吶ｋ縲・

    縺薙・蛟､縺ｯ繝・ヰ繧､繧ｹ蜀・Κ縺ｧ縺ｮ縺ｿ菴ｿ逕ｨ縺励∝､夜Κ・・RL繝ｻ繝ｭ繧ｰ遲会ｼ峨↓縺ｯ蜃ｺ縺輔↑縺・・
    繧ｵ繝ｼ繝仙・繧ょ酔縺倩ｨ育ｮ怜ｼ上〒 factory_token 繧貞・蟆主・縺励√◎縺ｮ繝上ャ繧ｷ繝･縺ｨ辣ｧ蜷医☆繧九・

    Raises:
        KeyError: 迺ｰ蠅・､画焚 FACTORY_SECRET 縺梧悴險ｭ螳壹・蝣ｴ蜷・
    """
    secret = os.environ["FACTORY_SECRET"]
    raw = f"{device_id}:{secret}".encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def derive_factory_token_hash(factory_token: str) -> str:
    """
    factory_token 繧偵＆繧峨↓繝上ャ繧ｷ繝･蛹悶＠縺ｦ factory_token_hash 繧貞ｰ主・縺吶ｋ縲・

    縺薙・蛟､縺ｮ縺ｿ QR 繧ｳ繝ｼ繝峨・ URL 繝代Λ繝｡繝ｼ繧ｿ (fth) 縺ｨ縺励※蜈ｬ髢九☆繧九・
    繧ｵ繝ｼ繝仙・縺ｧ縺ｯ菫晏ｭ俶ｸ医∩縺ｮ factory_token_hash 縺ｨ QR 縺ｮ fth 繧呈ｯ碑ｼ・・蜷医☆繧九・
    """
    return hashlib.sha256(factory_token.encode()).hexdigest()[:16]


def build_setup_url(device_id: str) -> str:
    """
    QR 繧ｳ繝ｼ繝峨↓蝓九ａ霎ｼ繧繧ｻ繝・ヨ繧｢繝・・ URL 繧堤ｵ・∩遶九※繧九・

    URL 縺ｫ縺ｯ factory_token_hash (fth) 縺ｮ縺ｿ蜷ｫ繧縲・
    factory_token・亥ｹｳ譁・ｼ峨・ URL 縺ｫ蜷ｫ繧√↑縺・・
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
    QR 繧ｳ繝ｼ繝峨ｒ逕滓・縺励※ PNG 縺ｫ菫晏ｭ倥☆繧九・

    Args:
        device_id:   蟇ｾ雎｡繝・ヰ繧､繧ｹ縺ｮ device_id
        output_path: QR 逕ｻ蜒上・菫晏ｭ伜・・医ョ繝輔か繝ｫ繝・ /etc/leonardo/qr_setup.png・・
        print_ascii: True 縺ｮ蝣ｴ蜷医√さ繝ｳ繧ｽ繝ｼ繝ｫ縺ｫ ASCII QR 繧貞・蜉帙☆繧・

    Returns:
        繧ｻ繝・ヨ繧｢繝・・ URL 譁・ｭ怜・
    """
    url = build_setup_url(device_id)

    # QR 繧ｳ繝ｼ繝峨が繝悶ず繧ｧ繧ｯ繝育函謌・
    qr = qrcode.QRCode(
        version=None,  # 繝・・繧ｿ驥上↓蠢懊§縺ｦ閾ｪ蜍輔し繧､繧ｺ豎ｺ螳・
        error_correction=qrcode.constants.ERROR_CORRECT_M,  # ~15% 隱､繧願ｨよｭ｣
        box_size=10,
        border=4,
    )
    qr.add_data(url)
    qr.make(fit=True)

    # PNG 菫晏ｭ・
    output_path.parent.mkdir(parents=True, exist_ok=True)
    img = qr.make_image(fill_color="black", back_color="white")
    img.save(str(output_path))

    # ASCII QR 蜃ｺ蜉幢ｼ磯幕逋ｺ繝ｻ迴ｾ蝣ｴ遒ｺ隱咲畑・・
    if print_ascii:
        print("\n--- ASCII QR (髢狗匱遒ｺ隱咲畑) ---")
        qr.print_ascii(invert=True)
        print(f"\nSetup URL : {url}")
        print(f"QR 菫晏ｭ伜・ : {output_path}")

    return url


def main() -> None:
    device_id_path = DEFAULT_DEVICE_ID_PATH

    if not device_id_path.exists():
        print(
            "Error: device_id 縺瑚ｦ九▽縺九ｊ縺ｾ縺帙ｓ縲ょ・縺ｫ generate_device_id.py 繧貞ｮ溯｡後＠縺ｦ縺上□縺輔＞縲・,
            file=sys.stderr,
        )
        sys.exit(1)

    device_id = device_id_path.read_text().strip()
    generate_qr(device_id)


if __name__ == "__main__":
    main()
