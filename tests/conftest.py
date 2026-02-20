"""
tests/conftest.py — pytest 共通フィクスチャ

ユニットテスト用:
  - 同期 TestClient（DB モック不要なエンドポイント確認用）

結合テスト用（integration マーク付きテスト）:
  - 実 PostgreSQL テスト DB に接続する AsyncClient
  - テーブルを session スコープで作成し、各テスト後にデータを TRUNCATE してクリーン状態を保つ
  - 通知・ジオロケーション等の外部 API はモックして CI でも動作可能にする

環境変数:
  TEST_DATABASE_URL  結合テスト用 PostgreSQL 接続文字列
  例: postgresql+asyncpg://postgres:password@localhost:5432/leonardo_jr_test
"""

import os
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from leonardo_api.database import Base, get_db
from leonardo_api.main import app

# ---------------------------------------------------------------------------
# デバイス側 generate_qr.py が os.environ["FACTORY_SECRET"] を参照するため、
# テスト実行時に設定する（サーバ側は settings.FACTORY_SECRET を使用）
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _factory_secret(monkeypatch):
    """FACTORY_SECRET をテスト用固定値で設定する。全テストに自動適用。"""
    monkeypatch.setenv("FACTORY_SECRET", "LEONARDO_JR_2026_SECRET")


# ---------------------------------------------------------------------------
# 結合テスト用 DB 接続 URL
# 環境変数が未設定の場合、integration テストはスキップされる
# ---------------------------------------------------------------------------
TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:password@localhost:5432/leonardo_jr_test",
)

_HAS_TEST_DB = "TEST_DATABASE_URL" in os.environ


# ===========================================================================
# ユニットテスト用フィクスチャ（DB 不要）
# ===========================================================================

@pytest.fixture
def client():
    """同期 TestClient。DB 不要なエンドポイントのテストに使用する。"""
    with TestClient(app) as c:
        yield c


# ===========================================================================
# 結合テスト用フィクスチャ
# ===========================================================================

@pytest_asyncio.fixture(scope="session")
async def integration_engine():
    """
    テスト DB の非同期エンジン。セッション全体で 1 つだけ作成する。

    テスト開始前にテーブルを作成し、テスト終了後に全テーブルを DROP する。
    TEST_DATABASE_URL が設定されていない場合はスキップする。
    """
    if not _HAS_TEST_DB:
        pytest.skip("TEST_DATABASE_URL が設定されていません。結合テストをスキップします。")

    engine = create_async_engine(TEST_DATABASE_URL, echo=False, pool_pre_ping=True)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)   # 前回のテストゴミを除去
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await engine.dispose()


@pytest_asyncio.fixture
async def integration_client(integration_engine):
    """
    結合テスト用の非同期 HTTP クライアント。

    `get_db` を test DB セッションで上書きし、テスト後にデータを TRUNCATE する。
    """
    TestSessionLocal = async_sessionmaker(
        integration_engine, expire_on_commit=False, class_=AsyncSession
    )

    async def override_get_db():
        async with TestSessionLocal() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c

    app.dependency_overrides.clear()

    # テスト後にデータをクリア（スキーマは保持）
    async with integration_engine.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE detection_events, location_history, devices, users"
                " RESTART IDENTITY CASCADE"
            )
        )
