import asyncio
import os
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

# RenderのDB接続情報（環境変数から取得、なければ直接指定）
# ※ここには手元の.envやPowerShellで設定した接続情報が使われます
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://leonardo_jr_db_user:L9cEJZUF05uzMxnVIgdYhnEdulqqCLag@dpg-d6evdis50q8c73afmtgg-a.singapore-postgres.render.com/leonardo_jr_db")

async def add_column():
    engine = create_async_engine(DATABASE_URL, echo=True)
    async with engine.begin() as conn:
        print("Checking if 'last_seen' column exists...")
        try:
            # カラム追加（存在しない場合のみ成功するように...といきたいが、
            # PostgreSQLの "ADD COLUMN IF NOT EXISTS" は便利。
            # タイムゾーン付きで作成します。
            await conn.execute(text("ALTER TABLE devices ADD COLUMN IF NOT EXISTS last_seen TIMESTAMP WITH TIME ZONE;"))
            print("Successfully added 'last_seen' column.")
        except Exception as e:
            print(f"Error: {e}")
    
    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(add_column())