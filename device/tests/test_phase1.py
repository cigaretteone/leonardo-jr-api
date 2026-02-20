"""
Phase 1 テストスイート
device_id 生成・factory_token 導出・QR URL 生成・init_device の各機能を検証する。

実行方法:
  cd device/tests
  pytest test_phase1.py -v

または scripts ディレクトリを PYTHONPATH に指定:
  PYTHONPATH=../scripts pytest test_phase1.py -v
"""

import hashlib
import sys
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

import pytest

# scripts ディレクトリを import パスに追加
SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import generate_device_id as did_mod
import generate_qr as qr_mod


# ─────────────────────────────────────────────
# 共通フィクスチャ
# ─────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _factory_secret(monkeypatch):
    """generate_qr.py が os.environ["FACTORY_SECRET"] を参照するため、テスト実行前に設定する。"""
    monkeypatch.setenv("FACTORY_SECRET", "LEONARDO_JR_2026_SECRET")


# ─────────────────────────────────────────────
# generate_device_id.py のテスト
# ─────────────────────────────────────────────


class TestGetCpuSerial:
    """get_cpu_serial() のテスト"""

    def test_returns_last8_uppercase(self, tmp_path):
        """Serial 行が存在する場合、下8桁を大文字で返す"""
        cpuinfo = tmp_path / "cpuinfo"
        cpuinfo.write_text(
            "Processor\t: ARMv7\nSerial\t\t: 0000000012a3f8b2c1\n"
        )
        result = did_mod.get_cpu_serial(cpuinfo)
        assert result == "A3F8B2C1"
        assert len(result) == 8

    def test_fallback_on_missing_file(self, tmp_path):
        """存在しないファイルを指定した場合は DEVDUMMY + 4桁サフィックスを返す"""
        result = did_mod.get_cpu_serial(tmp_path / "nonexistent")
        assert result.startswith("DEVDUMMY")
        assert len(result) == 12  # "DEVDUMMY"(8) + 4桁乱数

    def test_fallback_on_no_serial_line(self, tmp_path):
        """Serial 行がない cpuinfo の場合は DEVDUMMY + 4桁サフィックスを返す"""
        cpuinfo = tmp_path / "cpuinfo"
        cpuinfo.write_text("Processor\t: ARMv7\nBogoMIPS\t: 38.40\n")
        result = did_mod.get_cpu_serial(cpuinfo)
        assert result.startswith("DEVDUMMY")
        assert len(result) == 12

    def test_short_serial_fallback(self, tmp_path):
        """Serial が 8 桁未満の場合は DEVDUMMY + 4桁サフィックスを返す"""
        cpuinfo = tmp_path / "cpuinfo"
        cpuinfo.write_text("Serial\t\t: 1234\n")
        result = did_mod.get_cpu_serial(cpuinfo)
        assert result.startswith("DEVDUMMY")
        assert len(result) == 12


class TestGenerateRandom4:
    """generate_random_4() のテスト"""

    def test_length_is_4(self):
        result = did_mod.generate_random_4()
        assert len(result) == 4

    def test_only_uppercase_alphanumeric(self):
        import string

        charset = set(string.ascii_uppercase + string.digits)
        for _ in range(50):
            result = did_mod.generate_random_4()
            assert all(c in charset for c in result)

    def test_randomness(self):
        """50回生成して全て同じ値にならないことを確認（確率的テスト）"""
        results = {did_mod.generate_random_4() for _ in range(50)}
        # 50回中に少なくとも2種類以上の値が出るはず
        assert len(results) > 1


class TestGenerateDeviceId:
    """generate_device_id() のテスト"""

    def test_format(self, tmp_path):
        """device_id が LJ-XXXXXXXX-XXXX 形式であること"""
        cpuinfo = tmp_path / "cpuinfo"
        cpuinfo.write_text("Serial\t\t: 0000000012a3f8b2c1\n")
        did_path = tmp_path / "device_id"

        device_id = did_mod.generate_device_id(did_path, cpuinfo)

        parts = device_id.split("-")
        assert len(parts) == 3, f"Expected 3 parts, got: {device_id}"
        assert parts[0] == "LJ"
        assert len(parts[1]) == 8
        assert len(parts[2]) == 4

    def test_file_is_saved(self, tmp_path):
        """生成後に device_id ファイルが保存されること"""
        did_path = tmp_path / "device_id"
        device_id = did_mod.generate_device_id(did_path)

        assert did_path.exists()
        assert did_path.read_text().strip() == device_id

    def test_idempotent(self, tmp_path):
        """2回目の呼び出しで既存の device_id が再利用されること"""
        did_path = tmp_path / "device_id"

        first = did_mod.generate_device_id(did_path)
        second = did_mod.generate_device_id(did_path)

        assert first == second

    def test_preexisting_file_is_used(self, tmp_path):
        """既存ファイルがある場合はそのまま返すこと（上書きしない）"""
        did_path = tmp_path / "device_id"
        did_path.write_text("LJ-EXISTING-1234")

        result = did_mod.generate_device_id(did_path)
        assert result == "LJ-EXISTING-1234"

    def test_creates_parent_directory(self, tmp_path):
        """保存先ディレクトリが存在しない場合でも作成すること"""
        nested = tmp_path / "deep" / "nested"
        did_path = nested / "device_id"

        did_mod.generate_device_id(did_path)
        assert did_path.exists()


# ─────────────────────────────────────────────
# generate_qr.py のテスト
# ─────────────────────────────────────────────


class TestFactoryTokenDerivation:
    """factory_token / factory_token_hash 導出のテスト"""

    def test_factory_token_is_deterministic(self):
        """同じ device_id から常に同じ factory_token が生成されること"""
        t1 = qr_mod.derive_factory_token("LJ-A3F8B2C1-7294")
        t2 = qr_mod.derive_factory_token("LJ-A3F8B2C1-7294")
        assert t1 == t2

    def test_factory_token_length(self):
        """factory_token は16文字であること"""
        token = qr_mod.derive_factory_token("LJ-A3F8B2C1-7294")
        assert len(token) == 16

    def test_factory_token_hash_is_deterministic(self):
        """同じ factory_token から常に同じ hash が生成されること"""
        token = qr_mod.derive_factory_token("LJ-A3F8B2C1-7294")
        h1 = qr_mod.derive_factory_token_hash(token)
        h2 = qr_mod.derive_factory_token_hash(token)
        assert h1 == h2

    def test_factory_token_hash_differs_from_token(self):
        """factory_token と factory_token_hash が異なること（二重ハッシュの確認）"""
        token = qr_mod.derive_factory_token("LJ-A3F8B2C1-7294")
        fth = qr_mod.derive_factory_token_hash(token)
        assert token != fth

    def test_different_device_ids_yield_different_tokens(self):
        """異なる device_id から異なる factory_token が生成されること"""
        t1 = qr_mod.derive_factory_token("LJ-A3F8B2C1-7294")
        t2 = qr_mod.derive_factory_token("LJ-B4E9C3D2-8351")
        assert t1 != t2

    def test_server_side_verification(self):
        """
        サーバ側の照合フローをシミュレート:
          1. デバイスが factory_token → fth を生成して QR に埋め込む
          2. サーバが同じ式で factory_token を再導出し、そのハッシュと fth を比較
        """
        device_id = "LJ-A3F8B2C1-7294"

        # デバイス側の処理
        factory_token_device = qr_mod.derive_factory_token(device_id)
        fth_in_qr = qr_mod.derive_factory_token_hash(factory_token_device)

        # サーバ側の検証（同じロジックで再計算）
        factory_token_server = qr_mod.derive_factory_token(device_id)
        fth_expected = qr_mod.derive_factory_token_hash(factory_token_server)

        assert fth_in_qr == fth_expected


class TestBuildSetupUrl:
    """build_setup_url() のテスト"""

    def test_url_contains_device_id(self):
        """URL に device_id が含まれること"""
        device_id = "LJ-A3F8B2C1-7294"
        url = qr_mod.build_setup_url(device_id)
        assert f"device_id={device_id}" in url

    def test_url_contains_fth(self):
        """URL に fth パラメータが含まれること"""
        url = qr_mod.build_setup_url("LJ-A3F8B2C1-7294")
        assert "fth=" in url

    def test_factory_token_not_in_url(self):
        """factory_token（平文）が URL に含まれないこと（最重要セキュリティ検証）"""
        device_id = "LJ-A3F8B2C1-7294"
        factory_token = qr_mod.derive_factory_token(device_id)
        url = qr_mod.build_setup_url(device_id)

        assert factory_token not in url, (
            "factory_token が URL に含まれています！"
            "URL はブラウザ履歴・リファラ・サーバログに残るため、平文を含めてはいけません。"
        )

    def test_url_base(self):
        """URL がセットアップドメインで始まること"""
        url = qr_mod.build_setup_url("LJ-A3F8B2C1-7294")
        assert url.startswith("https://setup.leonardo-jr.jp/register")

    def test_url_is_deterministic(self):
        """同じ device_id から常に同じ URL が生成されること"""
        device_id = "LJ-A3F8B2C1-7294"
        assert qr_mod.build_setup_url(device_id) == qr_mod.build_setup_url(device_id)

    def test_fth_is_correct_hash(self):
        """URL 中の fth 値が期待する二重ハッシュ値と一致すること"""
        device_id = "LJ-A3F8B2C1-7294"
        url = qr_mod.build_setup_url(device_id)

        # fth パラメータを URL から取り出す
        from urllib.parse import parse_qs, urlparse

        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        fth_from_url = params["fth"][0]

        # 期待値を計算
        token = qr_mod.derive_factory_token(device_id)
        expected_fth = qr_mod.derive_factory_token_hash(token)

        assert fth_from_url == expected_fth


class TestGenerateQr:
    """generate_qr() のテスト（qrcode ライブラリが必要）"""

    pytest.importorskip("qrcode", reason="qrcode ライブラリが必要: pip install qrcode[pil]")

    def test_qr_saves_png(self, tmp_path):
        """QR コード PNG が指定パスに保存されること"""
        output = tmp_path / "test_qr.png"
        qr_mod.generate_qr("LJ-A3F8B2C1-7294", output, print_ascii=False)
        assert output.exists()
        assert output.stat().st_size > 0

    def test_qr_creates_parent_dir(self, tmp_path):
        """保存先ディレクトリが存在しない場合でも作成されること"""
        output = tmp_path / "deep" / "nested" / "qr.png"
        qr_mod.generate_qr("LJ-A3F8B2C1-7294", output, print_ascii=False)
        assert output.exists()

    def test_qr_returns_url(self, tmp_path):
        """generate_qr がセットアップ URL を返すこと"""
        output = tmp_path / "qr.png"
        url = qr_mod.generate_qr("LJ-A3F8B2C1-7294", output, print_ascii=False)
        assert url.startswith("https://setup.leonardo-jr.jp/register")
        assert "device_id=LJ-A3F8B2C1-7294" in url


# ─────────────────────────────────────────────
# init_device.py のテスト
# ─────────────────────────────────────────────


class TestInitDevice:
    """init_device.main() のテスト"""

    def test_full_init_flow(self, tmp_path, monkeypatch):
        """
        device_id もQRも存在しない状態からフル初期化が成功すること。
        qrcode が利用できない環境では QR 生成をスキップして終了コード 0 を返す。
        """
        import init_device as init_mod

        did_path = tmp_path / "device_id"
        qr_path = tmp_path / "qr_setup.png"
        cpuinfo = tmp_path / "cpuinfo"
        cpuinfo.write_text("Serial\t\t: 0000000012a3f8b2c1\n")

        # パスをテスト用一時ディレクトリに差し替え
        monkeypatch.setattr(did_mod, "DEFAULT_DEVICE_ID_PATH", did_path)
        monkeypatch.setattr(qr_mod, "DEFAULT_QR_PATH", qr_path)

        exit_code = init_mod.main()
        assert exit_code == 0
        assert did_path.exists()

    def test_idempotent_on_second_run(self, tmp_path, monkeypatch):
        """2回実行しても同じ device_id が維持されること"""
        import init_device as init_mod

        did_path = tmp_path / "device_id"
        monkeypatch.setattr(did_mod, "DEFAULT_DEVICE_ID_PATH", did_path)
        monkeypatch.setattr(qr_mod, "DEFAULT_QR_PATH", tmp_path / "qr.png")

        init_mod.main()
        first_id = did_path.read_text().strip()

        init_mod.main()
        second_id = did_path.read_text().strip()

        assert first_id == second_id
