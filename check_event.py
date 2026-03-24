import asyncio
from sqlalchemy import text
from leonardo_api.database import engine

async def check():
    async with engine.begin() as conn:
        r = await conn.execute(text(
            "SELECT event_id, device_id, detection_type, confidence, occurred_at "
            "FROM detection_events ORDER BY received_at DESC LIMIT 5"
        ))
        print("Recent events:")
        for row in r:
            print(f"  {row[0]} | {row[1]} | {row[2]} | {row[3]} | {row[4]}")

asyncio.run(check())