"""
database.py — DB エンジンとセッション管理

SQLAlchemy 2.0 非同期スタイルを使用。
FastAPI の依存注入（get_db）でルーターに渡す。
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from .config import settings

# 非同期エンジン
# pool_pre_ping=True: コネクションが死んでいたら再接続（長時間アイドル後の対策）
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
)

# セッションファクトリ
AsyncSessionLocal = async_sessionmaker(
    engine,
    expire_on_commit=False,  # commit 後もオブジェクトを読める
    class_=AsyncSession,
)


class Base(DeclarativeBase):
    """全モデルの基底クラス。"""
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI 依存注入用のセッションプロバイダ。

    例外発生時は自動ロールバックし、セッションを必ずクローズする。
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
