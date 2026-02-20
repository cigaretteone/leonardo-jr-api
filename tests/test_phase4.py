"""
tests/test_phase4.py — Phase 4 Webセットアップ画面テスト

HTML ファイルの構造・必須要素・JavaScript 関数の存在を検証する。
ブラウザなしで CI で実行できる静的解析テスト。

実行方法:
  pytest tests/test_phase4.py -v
"""

import pathlib
import re

import pytest

# テスト対象 HTML ファイル
SETUP_HTML_PATH = pathlib.Path(__file__).parent.parent / "web" / "setup.html"


@pytest.fixture(scope="module")
def html_source() -> str:
    """setup.html の内容を返す。"""
    assert SETUP_HTML_PATH.exists(), f"setup.html が見つかりません: {SETUP_HTML_PATH}"
    return SETUP_HTML_PATH.read_text(encoding="utf-8")


# ===========================================================================
# ファイル存在・基本構造
# ===========================================================================

class TestFileStructure:
    def test_file_exists(self):
        assert SETUP_HTML_PATH.exists()

    def test_is_valid_html(self, html_source):
        assert "<!DOCTYPE html>" in html_source
        assert "<html" in html_source
        assert "</html>" in html_source

    def test_charset_utf8(self, html_source):
        assert 'charset="UTF-8"' in html_source or "charset=UTF-8" in html_source

    def test_viewport_meta(self, html_source):
        """スマホ対応の viewport メタタグが存在すること"""
        assert 'name="viewport"' in html_source
        assert "width=device-width" in html_source

    def test_title(self, html_source):
        assert "Leonardo Jr." in html_source


# ===========================================================================
# 外部ライブラリ読み込み
# ===========================================================================

class TestExternalLibraries:
    def test_tailwind_cdn(self, html_source):
        assert "cdn.tailwindcss.com" in html_source

    def test_leaflet_css(self, html_source):
        assert "leaflet" in html_source.lower()
        assert ".css" in html_source

    def test_leaflet_js(self, html_source):
        assert "leaflet" in html_source.lower()
        assert ".js" in html_source


# ===========================================================================
# 5ステップの DOM 要素
# ===========================================================================

class TestStepElements:
    def test_all_five_steps_present(self, html_source):
        for i in range(1, 6):
            assert f'id="step-{i}"' in html_source, f"step-{i} が見つかりません"

    def test_progress_bar_segments(self, html_source):
        for i in range(1, 6):
            assert f'id="prog-{i}"' in html_source, f"prog-{i} が見つかりません"

    def test_step_label_element(self, html_source):
        assert 'id="step-label"' in html_source

    def test_error_banner(self, html_source):
        assert 'id="error-banner"' in html_source
        assert 'id="error-text"' in html_source


# ===========================================================================
# Step 1: ログイン / 新規登録フォーム
# ===========================================================================

class TestStep1Auth:
    def test_login_tab(self, html_source):
        assert 'id="tab-login"' in html_source

    def test_register_tab(self, html_source):
        assert 'id="tab-register"' in html_source

    def test_login_email_input(self, html_source):
        assert 'id="login-email"' in html_source

    def test_login_password_input(self, html_source):
        assert 'id="login-password"' in html_source

    def test_register_email_input(self, html_source):
        assert 'id="reg-email"' in html_source

    def test_register_password_input(self, html_source):
        assert 'id="reg-password"' in html_source

    def test_register_phone_input(self, html_source):
        assert 'id="reg-phone"' in html_source

    def test_login_button(self, html_source):
        assert 'id="btn-login"' in html_source

    def test_register_button(self, html_source):
        assert 'id="btn-register"' in html_source


# ===========================================================================
# Step 2: デバイス登録確認
# ===========================================================================

class TestStep2DeviceConfirm:
    def test_device_id_display(self, html_source):
        assert 'id="display-device-id"' in html_source

    def test_device_register_button(self, html_source):
        assert 'id="btn-device-register"' in html_source


# ===========================================================================
# Step 3: セットアップ（通知先・検知対象）
# ===========================================================================

class TestStep3Setup:
    def test_line_token_input(self, html_source):
        assert 'id="line-token"' in html_source

    def test_notify_email_input(self, html_source):
        assert 'id="notify-email"' in html_source

    def test_bear_checkbox(self, html_source):
        assert 'id="target-bear"' in html_source
        assert 'value="bear"' in html_source

    def test_human_checkbox(self, html_source):
        assert 'id="target-human"' in html_source
        assert 'value="human"' in html_source

    def test_vehicle_checkbox(self, html_source):
        assert 'id="target-vehicle"' in html_source
        assert 'value="vehicle"' in html_source

    def test_bear_checked_by_default(self, html_source):
        """熊はデフォルトでチェック済みであること（主用途）"""
        bear_section = html_source[html_source.find('id="target-bear"'):]
        # id="target-bear" の直近 80 文字以内に checked があること
        assert "checked" in bear_section[:80]

    def test_setup_button(self, html_source):
        assert 'id="btn-setup"' in html_source


# ===========================================================================
# Step 4: 位置登録（GPS・地図）
# ===========================================================================

class TestStep4Location:
    def test_map_container(self, html_source):
        assert 'id="map"' in html_source

    def test_accuracy_card(self, html_source):
        assert 'id="accuracy-card"' in html_source

    def test_accuracy_warning(self, html_source):
        assert 'id="accuracy-warning"' in html_source

    def test_get_location_button(self, html_source):
        assert 'id="btn-get-location"' in html_source

    def test_register_location_button(self, html_source):
        assert 'id="btn-register-location"' in html_source

    def test_register_location_button_starts_disabled(self, html_source):
        """位置登録ボタンは GPS 取得前は disabled であること"""
        segment = html_source[html_source.find('id="btn-register-location"') - 200:][:300]
        assert "disabled" in segment

    def test_accuracy_legend_present(self, html_source):
        """精度の凡例（緑/黄/赤）の説明が存在すること"""
        assert "50m" in html_source
        assert "100m" in html_source


# ===========================================================================
# Step 5: 完了画面
# ===========================================================================

class TestStep5Complete:
    def test_complete_device_id_display(self, html_source):
        assert 'id="complete-device-id"' in html_source

    def test_dashboard_link(self, html_source):
        assert 'id="dashboard-link"' in html_source
        assert "dashboard" in html_source


# ===========================================================================
# JavaScript 関数の存在確認
# ===========================================================================

class TestJavaScriptFunctions:
    """主要な関数が定義されていることを静的解析で確認する。"""

    JS_FUNCTIONS = [
        "function goToStep",
        "function switchTab",
        "function showError",
        "function hideError",
        "function setLoading",
        "async function apiFetch",
        "async function handleLogin",
        "async function handleRegister",
        "async function handleDeviceRegister",
        "async function handleSetup",
        "function initMap",
        "function getLocation",
        "function updateAccuracyUI",
        "async function handleLocationRegister",
    ]

    @pytest.mark.parametrize("func_signature", JS_FUNCTIONS)
    def test_function_exists(self, html_source, func_signature):
        assert func_signature in html_source, f"JS 関数が見つかりません: {func_signature}"


# ===========================================================================
# セキュリティ・設計上のチェック
# ===========================================================================

class TestSecurityDesign:
    def test_api_base_constant_exists(self, html_source):
        """API_BASE 定数が定義されていること"""
        assert "API_BASE" in html_source

    def test_fth_url_encoded(self, html_source):
        """fth を URL エンコードして送信していること（特殊文字対策）"""
        assert "encodeURIComponent(state.fth)" in html_source

    def test_device_id_url_encoded(self, html_source):
        """device_id を URL エンコードして送信していること"""
        assert "encodeURIComponent(state.deviceId)" in html_source

    def test_geolocation_high_accuracy(self, html_source):
        """GPS は高精度モードで取得すること"""
        assert "enableHighAccuracy: true" in html_source

    def test_geolocation_timeout_30s(self, html_source):
        """GPS タイムアウトが 30 秒であること（仕様 §4.4）"""
        assert "timeout: 30000" in html_source

    def test_no_api_token_in_localstorage(self, html_source):
        """api_token を localStorage に保存していないこと（セッション内のみ保持）"""
        assert "localStorage.setItem" not in html_source

    def test_authorization_bearer_header(self, html_source):
        """JWT を Authorization: Bearer ヘッダーで送信していること"""
        assert "Bearer" in html_source
        assert "Authorization" in html_source


# ===========================================================================
# FastAPI 配信エンドポイント確認
# ===========================================================================

class TestFastAPIServing:
    def test_setup_route_in_main(self):
        """main.py に /setup エンドポイントが追加されていること"""
        main_py = pathlib.Path(__file__).parent.parent / "leonardo_api" / "main.py"
        content = main_py.read_text(encoding="utf-8")
        assert '"/setup"' in content or "'/setup'" in content
        assert "setup.html" in content

    def test_web_dir_static_mount(self):
        """main.py に StaticFiles マウントが追加されていること"""
        main_py = pathlib.Path(__file__).parent.parent / "leonardo_api" / "main.py"
        content = main_py.read_text(encoding="utf-8")
        assert "StaticFiles" in content
