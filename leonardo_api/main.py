"""
main.py — FastAPI アプリケーションエントリーポイント

起動方法:
  uvicorn leonardo_api.main:app --reload --host 0.0.0.0 --port 8000

  Webセットアップ画面:
    http://localhost:8000/setup?device_id=LJ-XXXXXXXX-XXXX&fth=XXXXXXXXXXXXXXXX
"""

import pathlib

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .routers import auth_router, device_router, event_router, location_router

# リポジトリルート / web ディレクトリのパス
_WEB_DIR = pathlib.Path(__file__).parent.parent / "web"

app = FastAPI(
    title="Leonardo Jr. API",
    description="Leonardo Jr. QR初期設定 + 検知イベント受信 API（実証機版）",
    version="0.1.0",
)

# ---------------------------------------------------------------------------
# CORS（Webセットアップ画面からのリクエストを許可）
# 本番では setup.leonardo-jr.jp のみに絞ること
# ---------------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 実証機では全許可。量産機では制限する。
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# ルーター登録
# ---------------------------------------------------------------------------
app.include_router(auth_router.router, prefix="/api/v1/auth", tags=["認証"])
app.include_router(device_router.router, prefix="/api/v1/devices", tags=["デバイス"])
app.include_router(location_router.router, prefix="/api/v1/devices", tags=["位置登録"])
app.include_router(event_router.router, prefix="/api/v1/devices", tags=["検知イベント"])


@app.get("/health", tags=["ヘルスチェック"])
async def health() -> dict:
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Webセットアップ画面の配信
# QR コードのURL: /setup?device_id=...&fth=...
# web/ ディレクトリの静的ファイルをそのまま返す
# ---------------------------------------------------------------------------
@app.get("/setup", include_in_schema=False)
async def serve_setup() -> FileResponse:
    """QR コード読み取り後にスマホブラウザで開くセットアップ画面。"""
    return FileResponse(_WEB_DIR / "setup.html")

# web/ 配下のその他静的アセット（将来の画像・CSS追加に備える）
if _WEB_DIR.exists():
    app.mount("/web", StaticFiles(directory=str(_WEB_DIR)), name="web")

