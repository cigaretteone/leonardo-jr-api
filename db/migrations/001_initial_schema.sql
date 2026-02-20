-- =============================================================================
-- Leonardo Jr. 実証機 — 初期スキーマ
-- マイグレーション: 001_initial_schema.sql
-- 対象DB:          PostgreSQL 15 以上
-- 作成日:          2026-02-20
--
-- 設計方針:
--   - 実証機スコープを実装する（量産機 v1.2 への拡張パスを確保する設計）
--   - ENUM は使わず VARCHAR + CHECK 制約で定義（値追加時の ALTER TABLE ENUM が不要）
--   - JSONB ではなく VARCHAR(500) でシンプルに保持（実証機規模では十分）
--   - 量産機で追加予定のカラムはコメントで記載（device_tokens 分離等）
-- =============================================================================

BEGIN;

-- ---------------------------------------------------------------------------
-- 拡張: gen_random_uuid() は PostgreSQL 13 以上で標準搭載のため拡張不要
-- ---------------------------------------------------------------------------

-- ===========================================================================
-- 1. users
--    Leonardo Jr. のサービスアカウント。
--    実証機では SMS OTP なし、メール + パスワード + JWT 認証のみ。
-- ===========================================================================
CREATE TABLE users (
    user_id      UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    email        VARCHAR(255)  UNIQUE NOT NULL,
    phone_number VARCHAR(20),                         -- 通知用（実証機では SMS 認証なし）
    password_hash VARCHAR(255) NOT NULL,              -- bcrypt ハッシュ
    created_at   TIMESTAMPTZ   NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at   TIMESTAMPTZ   NOT NULL DEFAULT CURRENT_TIMESTAMP

    -- 量産機 v1.2 追加予定:
    --   sms_verified   BOOLEAN DEFAULT FALSE
    --   is_active      BOOLEAN DEFAULT TRUE
    --   plan_type      VARCHAR(20) DEFAULT 'consumer' （ユーザー単位プランに移行する場合）
);

COMMENT ON TABLE  users               IS 'サービスアカウント。実証機では JWT 認証のみ、SMS OTP なし。';
COMMENT ON COLUMN users.phone_number  IS '通知用電話番号。実証機では SMS 認証に使用しない。';
COMMENT ON COLUMN users.password_hash IS 'bcrypt でハッシュ化したパスワード。平文は保持しない。';

-- updated_at を自動更新するトリガー関数
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_users_updated_at
    BEFORE UPDATE ON users
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at();


-- ===========================================================================
-- 2. devices
--    Leonardo Jr. 実機の個体レコード。
--    owner_user_id IS NULL = QR 登録前の未所有状態。
--    devices レコードは QR 登録時に初めて作成される（pre-register なし）。
-- ===========================================================================
CREATE TABLE devices (
    device_id            VARCHAR(30)  PRIMARY KEY,
                                                     -- 例: LJ-A3F8B2C1-7294（CPUシリアル下8桁 + 4桁乱数）
    cpu_serial           VARCHAR(20),                -- Raspberry Pi CPUシリアル（トレーサビリティ用）
    owner_user_id        UUID         REFERENCES users(user_id),
                                                     -- NULL = 未登録（QR 登録前）
    factory_token_hash   VARCHAR(64)  NOT NULL,      -- sha256(factory_token)[:16]。QR の fth パラメータと照合する。
    api_token            VARCHAR(255),               -- デバイス発報用トークン。実証機では単一トークン。
                                                     -- 量産機 v1.2 では device_tokens テーブルに分離予定。
    token_expires_at     TIMESTAMPTZ,                -- api_token の有効期限（NULL = 無期限、実証機では使用しない）
    status               VARCHAR(20)  NOT NULL DEFAULT 'active'
                             CHECK (status IN ('active', 'suspended')),
                                                     -- 実証機では 'stolen' は省略（量産機 v1.2 で追加）
    plan_type            VARCHAR(20)  NOT NULL DEFAULT 'consumer'
                             CHECK (plan_type IN ('consumer', 'ultimate')),
    notification_target  VARCHAR(500),               -- 通知先 JSON（例: {"line_token":"xxx","email":"a@b.com"}）
    detection_targets    VARCHAR(500),               -- 検知対象 JSON（例: ["bear","human","vehicle"]）
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT CURRENT_TIMESTAMP

    -- 量産機 v1.2 追加予定:
    --   stolen_at           TIMESTAMPTZ
    --   last_heartbeat_at   TIMESTAMPTZ
    --   firmware_version    VARCHAR(20)
);

COMMENT ON TABLE  devices                   IS '実機個体レコード。QR 登録時に生成される（事前登録なし）。';
COMMENT ON COLUMN devices.device_id         IS 'LJ-{CPUシリアル下8桁}-{4桁乱数} 形式の個体 ID。';
COMMENT ON COLUMN devices.owner_user_id     IS 'NULL の場合は未登録状態。QR 登録後に設定される。';
COMMENT ON COLUMN devices.factory_token_hash IS 'QR の fth パラメータと照合する値。factory_token 平文はここに保存しない。';
COMMENT ON COLUMN devices.api_token         IS 'デバイスがイベント発報時に使用するトークン。量産機では device_tokens テーブルへ分離。';
COMMENT ON COLUMN devices.status            IS 'active: 通常稼働 / suspended: 停止中。量産機では stolen を追加予定。';
COMMENT ON COLUMN devices.notification_target IS '通知先を JSON 文字列で保持（LINE Notify トークン、メール等）。';
COMMENT ON COLUMN devices.detection_targets   IS '検知対象を JSON 配列文字列で保持（bear, human, vehicle 等）。';

-- オーナーでのルックアップ用インデックス（ダッシュボードでユーザーのデバイス一覧取得）
CREATE INDEX idx_devices_owner ON devices(owner_user_id);


-- ===========================================================================
-- 3. location_history
--    デバイス設置座標の履歴。
--    active_flag = TRUE が現在有効な設置座標。過去の座標は FALSE で履歴保持。
--    active_flag の切り替えはトランザクション内で一括実行する（仕様 §6.3）。
-- ===========================================================================
CREATE TABLE location_history (
    id            SERIAL        PRIMARY KEY,
    device_id     VARCHAR(30)   NOT NULL REFERENCES devices(device_id),
    lat           DECIMAL(10,8) NOT NULL,             -- 緯度  (-90.00000000 〜 +90.00000000)
    lon           DECIMAL(11,8) NOT NULL,             -- 経度 (-180.00000000 〜 +180.00000000)
    accuracy      DECIMAL(8,2),                       -- GPS 精度（メートル）。NULL 許可。
                                                      -- 50m 以下: OK / 50〜100m: 警告 / 100m 超: 警告（拒否しない）
    registered_at TIMESTAMPTZ   NOT NULL DEFAULT CURRENT_TIMESTAMP,
    registered_by UUID          NOT NULL REFERENCES users(user_id),
    active_flag   BOOLEAN       NOT NULL DEFAULT TRUE,
    ip_address    INET                                -- 登録リクエスト元 IP（ジオロケーション補助用）

    -- 量産機 v1.2 追加予定:
    --   altitude DECIMAL(8,2)     -- 標高（山間部設置）
    --   source   VARCHAR(20) DEFAULT 'gps'  -- 'gps' | 'manual'
);

COMMENT ON TABLE  location_history              IS '設置座標履歴。active_flag=TRUE が現在有効な座標。過去座標は FALSE で保持。';
COMMENT ON COLUMN location_history.accuracy     IS 'GPS 精度（m）。50m 超は警告表示するが登録は許可（山間部テスト対応）。';
COMMENT ON COLUMN location_history.active_flag  IS '現在有効な設置座標フラグ。切り替えは同一トランザクション内で実行すること。';
COMMENT ON COLUMN location_history.registered_by IS '座標を登録したユーザー。再設置時もログとして残る。';

-- デバイス別・日時降順での座標参照（現在地取得・履歴一覧）
CREATE INDEX idx_location_device_time ON location_history(device_id, registered_at DESC);

-- アクティブ座標だけを高速検索するための部分インデックス
CREATE INDEX idx_location_active ON location_history(device_id)
    WHERE active_flag = TRUE;

-- 1デバイスにつきアクティブ座標は必ず1件のみであることを DB レベルで保証する
-- UPDATE active_flag=FALSE → INSERT active_flag=TRUE の順に同一トランザクション内で実行すること（仕様 §6.3）
CREATE UNIQUE INDEX idx_one_active_per_device
    ON location_history(device_id) WHERE active_flag = TRUE;


-- ===========================================================================
-- 4. detection_events
--    AI が検知したイベントのログ。
--    発報時 IP のジオロケーション結果と登録座標との距離を保持し、
--    広域位置逸脱アラート（仕様 §7）の根拠情報とする。
-- ===========================================================================
CREATE TABLE detection_events (
    id                          SERIAL        PRIMARY KEY,
    device_id                   VARCHAR(30)   NOT NULL REFERENCES devices(device_id),
    detected_at                 TIMESTAMPTZ   NOT NULL DEFAULT CURRENT_TIMESTAMP,
    detection_type              VARCHAR(20),           -- 例: 'bear' | 'human' | 'vehicle'
    confidence                  DECIMAL(5,4),          -- AI 信頼度 (0.0000 〜 1.0000)
    image_path                  VARCHAR(500),          -- 検知画像のストレージパス
    ip_address                  INET,                  -- 発報時のデバイス IP
    ip_geolocation_region       VARCHAR(100),          -- IPジオロケーション結果（都道府県 or 地域名）
    distance_from_registered_km DECIMAL(10,3),         -- 登録座標との Haversine 距離（km）
    location_mismatch           BOOLEAN       NOT NULL DEFAULT FALSE
                                                       -- TRUE: 150km 超 または 都道府県不一致
    -- 量産機 v1.2 追加予定:
    --   notified_at  TIMESTAMPTZ          -- 通知送信日時
    --   offline_log  BOOLEAN DEFAULT FALSE -- 圏外ログからの一括アップロード分
);

COMMENT ON TABLE  detection_events                          IS 'AI 検知イベントログ。発報時 IP と登録座標の照合結果も保持する。';
COMMENT ON COLUMN detection_events.confidence               IS 'AI 信頼度スコア。0.0000〜1.0000。';
COMMENT ON COLUMN detection_events.ip_geolocation_region   IS 'ip-api.com 等で取得した地域名（都道府県レベル）。LTE NAT によるズレあり。';
COMMENT ON COLUMN detection_events.distance_from_registered_km IS '登録座標との Haversine 距離（km）。LTE IP ズレで数十km 単位の誤差あり。';
COMMENT ON COLUMN detection_events.location_mismatch       IS 'TRUE: 150km 超 または 都道府県不一致。所有者への逸脱アラートのトリガー。';

-- デバイス別・日時降順でのイベント参照（ダッシュボード・履歴一覧）
CREATE INDEX idx_detection_device_time ON detection_events(device_id, detected_at DESC);

-- 位置逸脱フラグが TRUE のイベントだけを高速検索する部分インデックス
CREATE INDEX idx_detection_mismatch ON detection_events(device_id, detected_at DESC)
    WHERE location_mismatch = TRUE;

COMMIT;
