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

from .routers import auth_router, device_router, event_router, location_router, media_router

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
app.include_router(event_router.router, prefix="/api/v1/devices", tags=["検知イベント"])
app.include_router(media_router.router, prefix="/api/v1/devices", tags=["メディア"])

@app.get("/health", tags=["ヘルスチェック"])
async def health() -> dict:
    import os
    from datetime import datetime, timezone
    from sqlalchemy import select, func
    from .database import get_db_context
    from .models import DetectionEvent, EventMedia
    from .config import MEDIA_STORAGE_PATH
    status = "ok"
    details = {}
    try:
        async with get_db_context() as db:
            total = (await db.execute(select(func.count()).select_from(DetectionEvent))).scalar() or 0
            details["total_events"] = total
            last = (await db.execute(select(DetectionEvent.occurred_at).order_by(DetectionEvent.occurred_at.desc()).limit(1))).scalar()
            details["last_event_at"] = last.isoformat() if last else None
            media_count = (await db.execute(select(func.count()).select_from(EventMedia).where(EventMedia.upload_status == "completed"))).scalar() or 0
            details["media_files"] = media_count
            details["db"] = "connected"
    except Exception as e:
        status = "degraded"
        details["db"] = f"error: {e}"
    try:
        if os.path.exists(MEDIA_STORAGE_PATH):
            total_size = sum(os.path.getsize(os.path.join(dp, f)) for dp, dn, fns in os.walk(MEDIA_STORAGE_PATH) for f in fns)
            details["media_disk_mb"] = round(total_size / 1048576, 1)
            details["media_disk_max_mb"] = 10240
    except Exception:
        pass
    details["server_time"] = datetime.now(timezone.utc).isoformat()
    return {"status": status, **details}


# ---------------------------------------------------------------------------
# Webセットアップ画面の配信
# QR コードのURL: /setup?device_id=...&fth=...
# web/ ディレクトリの静的ファイルをそのまま返す
# ---------------------------------------------------------------------------
@app.get("/events", include_in_schema=False)
async def serve_events() -> FileResponse:
    return FileResponse(_WEB_DIR / "events.html")
@app.get("/setup", include_in_schema=False)
async def serve_setup() -> FileResponse:
    """QR コード読み取り後にスマホブラウザで開くセットアップ画面。"""
    return FileResponse(_WEB_DIR / "setup.html")

# web/ 配下のその他静的アセット（将来の画像・CSS追加に備える）
if _WEB_DIR.exists():
    app.mount("/web", StaticFiles(directory=str(_WEB_DIR)), name="web")

