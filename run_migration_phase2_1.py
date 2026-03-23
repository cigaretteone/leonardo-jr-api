import asyncio
from sqlalchemy import text
from leonardo_api.database import engine

SQL1 = """
CREATE TABLE IF NOT EXISTS event_media (
    media_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id          UUID NOT NULL REFERENCES detection_events(event_id),
    media_type        VARCHAR(16) NOT NULL,
    upload_status     VARCHAR(16) NOT NULL DEFAULT 'completed',
    codec             VARCHAR(16),
    resolution        VARCHAR(16),
    duration_sec      NUMERIC(5,1),
    file_size_bytes   INTEGER,
    sha256_hash       VARCHAR(64),
    storage_path      VARCHAR(500),
    uploaded_at       TIMESTAMPTZ,
    created_at        TIMESTAMPTZ DEFAULT now(),
    CONSTRAINT uq_event_media UNIQUE(event_id, media_type),
    CONSTRAINT chk_upload_status CHECK (
        upload_status IN ('pending', 'uploading', 'completed', 'failed')
    ),
    CONSTRAINT chk_media_type CHECK (
        media_type IN ('thumbnail', 'video')
    )
)
"""

SQL2 = "CREATE INDEX IF NOT EXISTS idx_event_media_event ON event_media(event_id)"

SQL3 = "CREATE INDEX IF NOT EXISTS idx_event_media_pending ON event_media(upload_status) WHERE upload_status = 'pending'"

VERIFY = "SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'event_media' ORDER BY ordinal_position"

async def run():
    async with engine.begin() as conn:
        await conn.execute(text(SQL1))
        print("1/3 event_media table created")
        await conn.execute(text(SQL2))
        print("2/3 idx_event_media_event created")
        await conn.execute(text(SQL3))
        print("3/3 idx_event_media_pending created")
        result = await conn.execute(text(VERIFY))
        print("\nevent_media columns:")
        for row in result:
            print(f"  {row[0]:20s} {row[1]}")

asyncio.run(run())