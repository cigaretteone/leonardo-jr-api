"""
Phase 1.1 マイグレーション実行スクリプト
- detection_events: UUID v7 PK に再作成
- event_delivery: 新規作成
- idx_events_device_time: インデックス作成
"""
import re
import psycopg2
import psycopg2.extras


def get_dsn() -> str:
    from leonardo_api.config import settings
    url = settings.DATABASE_URL
    # postgresql+asyncpg:// → postgresql:// に変換
    url = re.sub(r"^postgresql\+asyncpg://", "postgresql://", url)
    return url


def main():
    dsn = get_dsn()
    print(f"[INFO] 接続先: {re.sub(r':([^:@]+)@', ':***@', dsn)}")

    conn = psycopg2.connect(dsn, sslmode="require")
    conn.autocommit = False
    cur = conn.cursor()

    # ── Step 1: レコード数確認 ──────────────────────────────────
    print("\n[STEP 1] detection_events レコード数確認...")
    cur.execute("SELECT COUNT(*) FROM detection_events")
    count = cur.fetchone()[0]
    print(f"  count = {count}")

    # ── Step 2: テーブル削除 or リネーム ────────────────────────
    if count == 0:
        print("\n[STEP 2] レコード 0 件 → DROP して再作成")
        cur.execute("DROP TABLE IF EXISTS event_delivery CASCADE")
        cur.execute("DROP TABLE IF EXISTS detection_events CASCADE")
    else:
        print(f"\n[STEP 2] レコード {count} 件 → detection_events_old にリネーム")
        cur.execute("DROP TABLE IF EXISTS event_delivery CASCADE")
        cur.execute("ALTER TABLE detection_events RENAME TO detection_events_old")

    conn.commit()

    # ── Step 3: detection_events 再作成 ─────────────────────────
    print("\n[STEP 3] detection_events テーブル作成...")
    cur.execute("""
        CREATE TABLE detection_events (
            event_id                    UUID        PRIMARY KEY,
            device_id                   VARCHAR(30) NOT NULL
                                        REFERENCES devices(device_id),
            event_type                  VARCHAR(32) NOT NULL
                                        DEFAULT 'detection',
            occurred_at                 TIMESTAMPTZ NOT NULL
                                        DEFAULT CURRENT_TIMESTAMP,
            received_at                 TIMESTAMPTZ NOT NULL
                                        DEFAULT CURRENT_TIMESTAMP,
            detection_type              VARCHAR(20),
            confidence                  NUMERIC(5,4),
            image_path                  VARCHAR(500),
            image_url                   VARCHAR(255),
            ip_address                  VARCHAR(45),
            ip_geolocation_region       VARCHAR(100),
            distance_from_registered_km NUMERIC(10,3),
            location_mismatch           BOOLEAN     NOT NULL DEFAULT FALSE,
            payload_json                JSONB
        )
    """)
    print("  OK")

    # ── Step 4: インデックス作成 ─────────────────────────────────
    print("\n[STEP 4] idx_events_device_time インデックス作成...")
    cur.execute("""
        CREATE INDEX idx_events_device_time
        ON detection_events (device_id, occurred_at)
    """)
    print("  OK")

    # ── Step 5: event_delivery テーブル作成 ─────────────────────
    print("\n[STEP 5] event_delivery テーブル作成...")
    cur.execute("""
        CREATE TABLE event_delivery (
            event_id        UUID        PRIMARY KEY
                            REFERENCES detection_events(event_id),
            state           VARCHAR(16) NOT NULL DEFAULT 'received',
            retry_count     INTEGER     NOT NULL DEFAULT 0,
            last_attempt_at TIMESTAMPTZ,
            acked_at        TIMESTAMPTZ,
            CONSTRAINT event_delivery_state_check
                CHECK (state IN ('received','processing','completed','failed'))
        )
    """)
    print("  OK")

    conn.commit()

    # ── Step 6: テーブル構造確認 ─────────────────────────────────
    print("\n[STEP 6] テーブル構造確認...")

    for table in ("detection_events", "event_delivery"):
        cur.execute("""
            SELECT column_name, data_type, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_name = %s
            ORDER BY ordinal_position
        """, (table,))
        rows = cur.fetchall()
        print(f"\n  \\d {table}")
        print(f"  {'Column':<35} {'Type':<20} {'Nullable':<10} Default")
        print("  " + "-" * 80)
        for col, dtype, nullable, default in rows:
            print(f"  {col:<35} {dtype:<20} {nullable:<10} {default or ''}")

    # インデックス確認
    cur.execute("""
        SELECT indexname, indexdef
        FROM pg_indexes
        WHERE tablename = 'detection_events'
    """)
    print("\n  Indexes on detection_events:")
    for idxname, idxdef in cur.fetchall():
        print(f"    {idxname}: {idxdef}")

    cur.close()
    conn.close()
    print("\n[DONE] Phase 1.1 マイグレーション完了")


if __name__ == "__main__":
    main()
