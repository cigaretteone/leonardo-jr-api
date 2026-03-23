"""
Phase 1.2 CRUD 動作確認テスト
  - DB接続確認
  - detection_events / event_delivery のテーブル構造確認
  - INSERT → SELECT → UPDATE → DELETE
  - UUID v7 PK、EventDelivery CHECK制約の検証
  - upload_logs 相当の一括挿入確認
"""
import asyncio
import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import IntegrityError

from leonardo_api.database import AsyncSessionLocal
from leonardo_api.models import DetectionEvent, EventDelivery

SEPARATOR = "=" * 60
PASS = "[PASS]"
FAIL = "[FAIL]"

# テスト用デバイスIDは実在する devices レコードが必要。
# まず devices テーブルから1件取得して使用する。
TEST_DEVICE_ID: str | None = None


async def setup_device_id(db) -> str | None:
    """テスト用デバイスIDを devices テーブルから取得"""
    r = await db.execute(text("SELECT device_id FROM devices LIMIT 1"))
    row = r.fetchone()
    return row[0] if row else None


# =============================================================================
# [1] DB接続確認
# =============================================================================

async def test_connection(db) -> bool:
    print(f"\n{SEPARATOR}")
    print("[1] DB接続確認")
    print(SEPARATOR)
    r = await db.execute(text("SELECT version()"))
    version = r.scalar()
    print(f"  PostgreSQL: {version[:60]}...")
    print(f"  {PASS} 接続成功")
    return True


# =============================================================================
# [2] テーブル構造確認
# =============================================================================

async def test_table_structure(db) -> bool:
    print(f"\n{SEPARATOR}")
    print("[2] テーブル構造確認")
    print(SEPARATOR)
    ok = True

    for table in ("detection_events", "event_delivery"):
        r = await db.execute(text("""
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_name = :t
            ORDER BY ordinal_position
        """), {"t": table})
        cols = r.fetchall()
        print(f"\n  {table} ({len(cols)} columns):")
        for col, dtype, nullable in cols:
            print(f"    {col:<35} {dtype:<25} nullable={nullable}")
        if not cols:
            print(f"  {FAIL} テーブルが存在しない")
            ok = False

    # インデックス確認
    r = await db.execute(text("""
        SELECT indexname FROM pg_indexes
        WHERE tablename = 'detection_events'
    """))
    idxs = [row[0] for row in r.fetchall()]
    print(f"\n  detection_events indexes: {idxs}")
    if "idx_events_device_time" in idxs:
        print(f"  {PASS} idx_events_device_time 存在確認")
    else:
        print(f"  {FAIL} idx_events_device_time が存在しない")
        ok = False

    return ok


# =============================================================================
# [3] INSERT テスト
# =============================================================================

async def test_insert(db, device_id: str) -> uuid.UUID | None:
    print(f"\n{SEPARATOR}")
    print("[3] INSERT テスト")
    print(SEPARATOR)

    event_id = uuid.uuid4()  # 実機では UUID v7、テストでは v4 で代用
    now = datetime.now(timezone.utc)

    try:
        event = DetectionEvent(
            event_id=event_id,
            device_id=device_id,
            event_type="detection",
            occurred_at=now,
            received_at=now,
            detection_type="bear",
            confidence=0.9231,
            ip_address="1.2.3.4",
            location_mismatch=False,
            payload_json={"test": True, "source": "phase1_2_crud_test"},
        )
        db.add(event)
        await db.flush()

        delivery = EventDelivery(
            event_id=event_id,
            state="received",
            retry_count=0,
            acked_at=now,
        )
        db.add(delivery)
        await db.commit()

        print(f"  event_id   : {event_id}")
        print(f"  device_id  : {device_id}")
        print(f"  event_type : detection")
        print(f"  {PASS} detection_events INSERT 成功")
        print(f"  {PASS} event_delivery INSERT 成功")
        return event_id

    except Exception as e:
        await db.rollback()
        print(f"  {FAIL} INSERT エラー: {e}")
        return None


# =============================================================================
# [4] SELECT テスト
# =============================================================================

async def test_select(db, event_id: uuid.UUID) -> bool:
    print(f"\n{SEPARATOR}")
    print("[4] SELECT テスト")
    print(SEPARATOR)

    # detection_events
    r = await db.execute(
        select(DetectionEvent).where(DetectionEvent.event_id == event_id)
    )
    event = r.scalar_one_or_none()
    if event is None:
        print(f"  {FAIL} detection_events: レコードが見つからない")
        return False
    print(f"  detection_events:")
    print(f"    event_id         = {event.event_id}")
    print(f"    event_type       = {event.event_type}")
    print(f"    detection_type   = {event.detection_type}")
    print(f"    confidence       = {event.confidence}")
    print(f"    location_mismatch= {event.location_mismatch}")
    print(f"    payload_json     = {event.payload_json}")
    print(f"  {PASS} detection_events SELECT 成功")

    # event_delivery
    r = await db.execute(
        select(EventDelivery).where(EventDelivery.event_id == event_id)
    )
    delivery = r.scalar_one_or_none()
    if delivery is None:
        print(f"  {FAIL} event_delivery: レコードが見つからない")
        return False
    print(f"\n  event_delivery:")
    print(f"    state       = {delivery.state}")
    print(f"    retry_count = {delivery.retry_count}")
    print(f"    acked_at    = {delivery.acked_at}")
    print(f"  {PASS} event_delivery SELECT 成功")
    return True


# =============================================================================
# [5] UPDATE テスト
# =============================================================================

async def test_update(db, event_id: uuid.UUID) -> bool:
    print(f"\n{SEPARATOR}")
    print("[5] UPDATE テスト")
    print(SEPARATOR)

    try:
        await db.execute(
            update(EventDelivery)
            .where(EventDelivery.event_id == event_id)
            .values(state="completed", retry_count=1)
        )
        await db.commit()

        r = await db.execute(
            select(EventDelivery).where(EventDelivery.event_id == event_id)
        )
        d = r.scalar_one_or_none()
        if d and d.state == "completed" and d.retry_count == 1:
            print(f"  state → 'completed', retry_count → 1")
            print(f"  {PASS} event_delivery UPDATE 成功")
        else:
            print(f"  {FAIL} UPDATE後の値が期待と異なる: state={d.state if d else None}")
            return False
        return True
    except Exception as e:
        await db.rollback()
        print(f"  {FAIL} UPDATE エラー: {e}")
        return False


# =============================================================================
# [6] CHECK制約テスト（不正な state を拒否するか）
# =============================================================================

async def test_check_constraint(db, device_id: str) -> bool:
    print(f"\n{SEPARATOR}")
    print("[6] CHECK制約テスト（不正な state を拒否）")
    print(SEPARATOR)

    bad_event_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    try:
        event = DetectionEvent(
            event_id=bad_event_id,
            device_id=device_id,
            event_type="detection",
            occurred_at=now,
            received_at=now,
            location_mismatch=False,
        )
        db.add(event)
        await db.flush()

        bad_delivery = EventDelivery(
            event_id=bad_event_id,
            state="INVALID_STATE",  # ← 不正値
            retry_count=0,
        )
        db.add(bad_delivery)
        await db.flush()
        await db.commit()

        # ここに到達したら制約が効いていない
        print(f"  {FAIL} CHECK制約が機能していない（不正 state が挿入された）")
        await db.rollback()
        return False

    except Exception as e:
        await db.rollback()
        if "event_delivery_state_check" in str(e) or "check" in str(e).lower():
            print(f"  CHECK制約エラー（期待通り）: {type(e).__name__}")
            print(f"  {PASS} CHECK制約が正しく機能")
            return True
        else:
            print(f"  {FAIL} 予期しないエラー: {e}")
            return False


# =============================================================================
# [7] 冪等性テスト（同一 event_id の再挿入）
# =============================================================================

async def test_idempotency(db, event_id: uuid.UUID, device_id: str) -> bool:
    print(f"\n{SEPARATOR}")
    print("[7] 冪等性テスト（ON CONFLICT DO NOTHING）")
    print(SEPARATOR)

    now = datetime.now(timezone.utc)
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    stmt = (
        pg_insert(DetectionEvent)
        .values(
            event_id=event_id,  # 既存の event_id
            device_id=device_id,
            event_type="detection",
            occurred_at=now,
            received_at=now,
            location_mismatch=False,
        )
        .on_conflict_do_nothing(index_elements=["event_id"])
        .returning(DetectionEvent.event_id)
    )
    r = await db.execute(stmt)
    row = r.fetchone()
    await db.commit()

    if row is None:
        print(f"  RETURNING が空 → 重複として無視された（期待通り）")
        print(f"  {PASS} ON CONFLICT DO NOTHING 動作確認")
        return True
    else:
        print(f"  {FAIL} 重複 event_id が新規挿入された")
        return False


# =============================================================================
# [8] bulk upload_logs 相当テスト
# =============================================================================

async def test_bulk_insert(db, device_id: str) -> list[uuid.UUID]:
    print(f"\n{SEPARATOR}")
    print("[8] bulk INSERT テスト（upload_logs 相当）")
    print(SEPARATOR)

    now = datetime.now(timezone.utc)
    items = [
        {"detection_type": "bear", "confidence": 0.85, "occurred_at": now},
        {"detection_type": "human", "confidence": 0.70, "occurred_at": now},
        {"detection_type": "vehicle", "confidence": 0.60, "occurred_at": now},
    ]

    eids = []
    events = []
    deliveries = []
    for item in items:
        eid = uuid.uuid4()
        eids.append(eid)
        events.append(DetectionEvent(
            event_id=eid,
            device_id=device_id,
            event_type="detection",
            occurred_at=item["occurred_at"],
            received_at=now,
            detection_type=item["detection_type"],
            confidence=item["confidence"],
            location_mismatch=False,
        ))
        deliveries.append(EventDelivery(
            event_id=eid,
            state="received",
            retry_count=0,
            acked_at=now,
        ))

    try:
        db.add_all(events)
        await db.flush()
        db.add_all(deliveries)
        await db.commit()
        print(f"  {len(eids)} 件挿入")
        print(f"  {PASS} bulk INSERT 成功")
        return eids
    except Exception as e:
        await db.rollback()
        print(f"  {FAIL} bulk INSERT エラー: {e}")
        return []


# =============================================================================
# [9] DELETE テスト（テストデータ全削除）
# =============================================================================

async def test_delete(db, all_eids: list[uuid.UUID]) -> bool:
    print(f"\n{SEPARATOR}")
    print("[9] DELETE テスト（テストデータ削除）")
    print(SEPARATOR)

    try:
        # FK制約: event_delivery → detection_events の順で削除
        await db.execute(
            delete(EventDelivery).where(EventDelivery.event_id.in_(all_eids))
        )
        await db.execute(
            delete(DetectionEvent).where(DetectionEvent.event_id.in_(all_eids))
        )
        await db.commit()

        # 削除確認
        r = await db.execute(
            select(DetectionEvent).where(DetectionEvent.event_id.in_(all_eids))
        )
        remaining = r.fetchall()
        if not remaining:
            print(f"  {len(all_eids)} 件のテストデータ削除完了")
            print(f"  {PASS} DELETE 成功")
            return True
        else:
            print(f"  {FAIL} {len(remaining)} 件が残っている")
            return False
    except Exception as e:
        await db.rollback()
        print(f"  {FAIL} DELETE エラー: {e}")
        return False


# =============================================================================
# [10] 最終カウント確認
# =============================================================================

async def test_final_count(db) -> None:
    print(f"\n{SEPARATOR}")
    print("[10] 最終レコード数確認")
    print(SEPARATOR)

    for table in ("detection_events", "detection_events_old", "event_delivery"):
        try:
            r = await db.execute(text(f"SELECT COUNT(*) FROM {table}"))
            print(f"  {table}: {r.scalar()} 件")
        except Exception:
            print(f"  {table}: （テーブルなし）")


# =============================================================================
# メイン
# =============================================================================

async def main():
    print(f"\n{'#' * 60}")
    print("  Phase 1.2 CRUD テスト開始")
    print(f"{'#' * 60}")

    results = {}

    async with AsyncSessionLocal() as db:
        # デバイスID取得
        device_id = await setup_device_id(db)
        if not device_id:
            print("\n[ERROR] devices テーブルにレコードがありません。テスト中断。")
            print("  → デバイスを1件登録してから再実行してください。")
            return

        print(f"\n  テスト用 device_id: {device_id}")

        results["接続"] = await test_connection(db)
        results["テーブル構造"] = await test_table_structure(db)

        event_id = await test_insert(db, device_id)
        results["INSERT"] = event_id is not None

        if event_id:
            results["SELECT"] = await test_select(db, event_id)
            results["UPDATE"] = await test_update(db, event_id)
            results["CHECK制約"] = await test_check_constraint(db, device_id)
            results["冪等性"] = await test_idempotency(db, event_id, device_id)

            bulk_eids = await test_bulk_insert(db, device_id)
            results["bulk INSERT"] = bool(bulk_eids)

            all_eids = [event_id] + bulk_eids
            results["DELETE"] = await test_delete(db, all_eids)

        await test_final_count(db)

    # ── サマリー ────────────────────────────────────────────────
    print(f"\n{'#' * 60}")
    print("  テスト結果サマリー")
    print(f"{'#' * 60}")
    total = len(results)
    passed = sum(1 for v in results.values() if v)
    for name, result in results.items():
        mark = PASS if result else FAIL
        print(f"  {mark} {name}")
    print(f"\n  {passed}/{total} テスト通過")
    if passed == total:
        print("  [OK] 全テスト成功 -- Phase 1.2 動作確認完了")
    else:
        print("  [NG] 一部テスト失敗 -- ログを確認してください")


if __name__ == "__main__":
    asyncio.run(main())
