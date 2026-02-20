"""
pytest 設定ファイル

scripts ディレクトリを sys.path に追加し、
device/scripts/*.py を import できるようにする。
"""

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
