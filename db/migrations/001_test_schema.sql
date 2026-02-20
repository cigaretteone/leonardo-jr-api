-- =============================================================================
-- Leonardo Jr. 実証機 — スキーマ検証スクリプト
-- ファイル: 001_test_schema.sql
--
-- 実行方法:
--   psql -U postgres -d leonardo_jr_dev -f 001_test_schema.sql
--
-- 001_initial_schema.sql 適用後にこのスクリプトを実行し、
-- テストが全て PASS することを確認する。
-- テスト後はロールバックするため、本番 DB には絶対に実行しないこと。
-- =============================================================================

BEGIN;

-- ---------------------------------------------------------------------------
-- ヘルパー: テスト結果を表示する関数
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION assert_true(
    p_test_name TEXT,
    p_condition BOOLEAN
) RETURNS VOID AS $$
BEGIN
    IF p_condition THEN
        RAISE NOTICE '[PASS] %', p_test_name;
    ELSE
        RAISE EXCEPTION '[FAIL] %', p_test_name;
    END IF;
END;
$$ LANGUAGE plpgsql;

RAISE NOTICE '=== Phase 2 スキーマ検証テスト開始 ===';

-- ===========================================================================
-- テスト 1: テーブル存在確認
-- ===========================================================================
PERFORM assert_true(
    'users テーブルが存在する',
    EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'users')
);

PERFORM assert_true(
    'devices テーブルが存在する',
    EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'devices')
);

PERFORM assert_true(
    'location_history テーブルが存在する',
    EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'location_history')
);

PERFORM assert_true(
    'detection_events テーブルが存在する',
    EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'detection_events')
);

-- ===========================================================================
-- テスト 2: users — 基本 INSERT / UUID 自動生成
-- ===========================================================================
INSERT INTO users (email, password_hash)
    VALUES ('test@example.com', '$2b$12$dummyhash_for_test_only')
    RETURNING user_id INTO STRICT _user_id;  -- STRICT: 1行でなければ例外

-- user_id が UUID 形式であることを確認（ハイフン区切り 36文字）
PERFORM assert_true(
    'users.user_id が UUID 形式で自動生成される',
    length(_user_id::TEXT) = 36
);

-- ===========================================================================
-- テスト 3: users — email UNIQUE 制約
-- ===========================================================================
DO $$
BEGIN
    INSERT INTO users (email, password_hash)
        VALUES ('test@example.com', 'dummy');
    RAISE EXCEPTION '[FAIL] users.email UNIQUE 制約が機能していない';
EXCEPTION
    WHEN unique_violation THEN
        RAISE NOTICE '[PASS] users.email UNIQUE 制約が正しく機能する';
END;
$$;

-- ===========================================================================
-- テスト 4: users — updated_at トリガー
-- ===========================================================================
DO $$
DECLARE
    _before TIMESTAMPTZ;
    _after  TIMESTAMPTZ;
BEGIN
    SELECT updated_at INTO _before FROM users WHERE email = 'test@example.com';
    PERFORM pg_sleep(0.01);  -- 最小待機でタイムスタンプ差を作る
    UPDATE users SET phone_number = '090-0000-0000' WHERE email = 'test@example.com';
    SELECT updated_at INTO _after FROM users WHERE email = 'test@example.com';

    PERFORM assert_true(
        'users.updated_at トリガーが UPDATE 時に自動更新される',
        _after > _before
    );
END;
$$;

-- ===========================================================================
-- テスト 5: devices — QR 登録フロー（owner_user_id = NULL で初期作成）
-- ===========================================================================
DO $$
DECLARE
    _user_id UUID;
BEGIN
    SELECT user_id INTO _user_id FROM users WHERE email = 'test@example.com';

    -- QR 登録フロー: owner 未設定で devices レコード作成
    INSERT INTO devices (device_id, factory_token_hash)
        VALUES ('LJ-TESTTEST-0001', 'abc123def456abcd');

    PERFORM assert_true(
        'devices: owner_user_id = NULL で初期レコード作成できる（未登録状態）',
        (SELECT owner_user_id IS NULL FROM devices WHERE device_id = 'LJ-TESTTEST-0001')
    );

    -- owner を設定（QR 登録完了）
    UPDATE devices SET owner_user_id = _user_id WHERE device_id = 'LJ-TESTTEST-0001';

    PERFORM assert_true(
        'devices: owner_user_id を設定できる（QR 登録完了フロー）',
        (SELECT owner_user_id = _user_id FROM devices WHERE device_id = 'LJ-TESTTEST-0001')
    );
END;
$$;

-- ===========================================================================
-- テスト 6: devices — status CHECK 制約
-- ===========================================================================
DO $$
BEGIN
    INSERT INTO devices (device_id, factory_token_hash, status)
        VALUES ('LJ-BADSTAT-0001', 'dummy', 'stolen');  -- 実証機では不可
    RAISE EXCEPTION '[FAIL] devices.status CHECK 制約が機能していない';
EXCEPTION
    WHEN check_violation THEN
        RAISE NOTICE '[PASS] devices.status CHECK 制約（stolen は実証機では不可）が正しく機能する';
END;
$$;

-- ===========================================================================
-- テスト 7: devices — plan_type CHECK 制約
-- ===========================================================================
DO $$
BEGIN
    INSERT INTO devices (device_id, factory_token_hash, plan_type)
        VALUES ('LJ-BADPLAN-0001', 'dummy', 'enterprise');
    RAISE EXCEPTION '[FAIL] devices.plan_type CHECK 制約が機能していない';
EXCEPTION
    WHEN check_violation THEN
        RAISE NOTICE '[PASS] devices.plan_type CHECK 制約が正しく機能する';
END;
$$;

-- ===========================================================================
-- テスト 8: location_history — active_flag トランザクション制御
-- ===========================================================================
DO $$
DECLARE
    _user_id  UUID;
    _loc1_id  INT;
    _loc2_id  INT;
BEGIN
    SELECT user_id INTO _user_id FROM users WHERE email = 'test@example.com';

    -- 1回目の位置登録
    INSERT INTO location_history (device_id, lat, lon, accuracy, registered_by)
        VALUES ('LJ-TESTTEST-0001', 35.68950000, 139.69170000, 12.50, _user_id)
        RETURNING id INTO _loc1_id;

    PERFORM assert_true(
        'location_history: 1件目の active_flag = TRUE で挿入される',
        (SELECT active_flag FROM location_history WHERE id = _loc1_id)
    );

    -- 2回目の位置登録（再設置フロー: 既存を FALSE に、新規を TRUE で）
    UPDATE location_history
        SET active_flag = FALSE
        WHERE device_id = 'LJ-TESTTEST-0001' AND active_flag = TRUE;

    INSERT INTO location_history (device_id, lat, lon, accuracy, registered_by)
        VALUES ('LJ-TESTTEST-0001', 34.69890000, 135.50230000, 8.20, _user_id)
        RETURNING id INTO _loc2_id;

    PERFORM assert_true(
        'location_history: 再設置後、旧座標の active_flag = FALSE になる',
        NOT (SELECT active_flag FROM location_history WHERE id = _loc1_id)
    );

    PERFORM assert_true(
        'location_history: 再設置後、新座標の active_flag = TRUE になる',
        (SELECT active_flag FROM location_history WHERE id = _loc2_id)
    );

    PERFORM assert_true(
        'location_history: active な座標が 1 件のみである',
        (SELECT COUNT(*) FROM location_history
         WHERE device_id = 'LJ-TESTTEST-0001' AND active_flag = TRUE) = 1
    );
END;
$$;

-- ===========================================================================
-- テスト 9: location_history — accuracy は NULL 許可（山間部対応）
-- ===========================================================================
DO $$
DECLARE
    _user_id UUID;
BEGIN
    SELECT user_id INTO _user_id FROM users WHERE email = 'test@example.com';

    -- accuracy = NULL で登録できること
    INSERT INTO location_history (device_id, lat, lon, accuracy, registered_by)
        VALUES ('LJ-TESTTEST-0001', 35.00000000, 135.00000000, NULL, _user_id);

    PERFORM assert_true(
        'location_history: accuracy = NULL で登録できる（デバイスが精度を返さない場合）',
        TRUE
    );
END;
$$;

-- ===========================================================================
-- テスト 10: detection_events — イベント記録と location_mismatch
-- ===========================================================================
DO $$
DECLARE
    _evt_id INT;
BEGIN
    -- 通常の検知イベント
    INSERT INTO detection_events
        (device_id, detection_type, confidence, ip_address,
         ip_geolocation_region, distance_from_registered_km, location_mismatch)
    VALUES
        ('LJ-TESTTEST-0001', 'bear', 0.9312, '203.0.113.1',
         '長野県', 2.400, FALSE)
    RETURNING id INTO _evt_id;

    PERFORM assert_true(
        'detection_events: 通常検知イベントが挿入できる',
        _evt_id IS NOT NULL
    );

    -- 位置逸脱イベント（150km 超）
    INSERT INTO detection_events
        (device_id, detection_type, confidence, ip_address,
         ip_geolocation_region, distance_from_registered_km, location_mismatch)
    VALUES
        ('LJ-TESTTEST-0001', 'human', 0.7851, '198.51.100.1',
         '大阪府', 212.300, TRUE);

    PERFORM assert_true(
        'detection_events: location_mismatch=TRUE のイベントが挿入できる',
        (SELECT COUNT(*) FROM detection_events
         WHERE device_id = 'LJ-TESTTEST-0001' AND location_mismatch = TRUE) = 1
    );
END;
$$;

-- ===========================================================================
-- テスト 11: detection_events — devices への外部キー制約
-- ===========================================================================
DO $$
BEGIN
    INSERT INTO detection_events (device_id, detection_type)
        VALUES ('LJ-NOTEXIST-9999', 'bear');
    RAISE EXCEPTION '[FAIL] detection_events の外部キー制約が機能していない';
EXCEPTION
    WHEN foreign_key_violation THEN
        RAISE NOTICE '[PASS] detection_events.device_id 外部キー制約が正しく機能する';
END;
$$;

-- ===========================================================================
-- テスト 12: インデックス存在確認
-- ===========================================================================
PERFORM assert_true(
    'idx_location_device_time インデックスが存在する',
    EXISTS (
        SELECT 1 FROM pg_indexes
        WHERE indexname = 'idx_location_device_time'
    )
);

PERFORM assert_true(
    'idx_location_active インデックスが存在する',
    EXISTS (
        SELECT 1 FROM pg_indexes
        WHERE indexname = 'idx_location_active'
    )
);

PERFORM assert_true(
    'idx_detection_device_time インデックスが存在する',
    EXISTS (
        SELECT 1 FROM pg_indexes
        WHERE indexname = 'idx_detection_device_time'
    )
);

PERFORM assert_true(
    'idx_detection_mismatch インデックスが存在する',
    EXISTS (
        SELECT 1 FROM pg_indexes
        WHERE indexname = 'idx_detection_mismatch'
    )
);

RAISE NOTICE '=== Phase 2 スキーマ検証テスト完了 ===';

-- テストデータを全て破棄（本番 DB への影響を防ぐ）
ROLLBACK;
