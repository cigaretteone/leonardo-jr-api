import asyncio
from sqlalchemy import text
from leonardo_api.database import AsyncSessionLocal

async def main():
    async with AsyncSessionLocal() as db:
        result = await db.execute(text("SELECT COUNT(*) FROM detection_events"))
        print("count =", result.scalar())

asyncio.run(main())
