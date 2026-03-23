import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

DATABASE_URL = "postgresql+asyncpg://leonardo_jr_db_user:L9cEJZUF05uzMxnVIgdYhnEdulqqCLag@dpg-d6evdis50q8c73afmtgg-a.singapore-postgres.render.com/leonardo_jr_db"

async def fix():
    engine = create_async_engine(DATABASE_URL)
    async with engine.begin() as conn:
        await conn.execute(text("ALTER TABLE detection_events ALTER COLUMN ip_address TYPE VARCHAR(45) USING ip_address::VARCHAR;"))
        print("OK")
    await engine.dispose()

asyncio.run(fix())