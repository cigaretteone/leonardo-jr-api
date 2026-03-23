import asyncio, os
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://leonardo_jr_db_user:L9cEJZUF05uzMxnVIgdYhnEdulqqCLag@dpg-d6evdis50q8c73afmtgg-a.singapore-postgres.render.com/leonardo_jr_db")

ALTERS = [
    "ALTER TABLE detection_events ADD COLUMN IF NOT EXISTS image_url VARCHAR(255);",
    "ALTER TABLE detection_events ADD COLUMN IF NOT EXISTS ip_address VARCHAR(45);",
    "ALTER TABLE detection_events ADD COLUMN IF NOT EXISTS ip_geolocation_region VARCHAR(100);",
    "ALTER TABLE detection_events ADD COLUMN IF NOT EXISTS distance_from_registered_km FLOAT;",
    "ALTER TABLE detection_events ADD COLUMN IF NOT EXISTS location_mismatch BOOLEAN DEFAULT FALSE;",
]

async def migrate():
    engine = create_async_engine(DATABASE_URL, echo=True)
    async with engine.begin() as conn:
        for sql in ALTERS:
            print(f"Executing: {sql}")
            await conn.execute(text(sql))
            print("OK")
    await engine.dispose()

asyncio.run(migrate())