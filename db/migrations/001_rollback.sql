-- =============================================================================
-- Leonardo Jr. 実証機 — ロールバック
-- マイグレーション: 001_rollback.sql
--
-- 001_initial_schema.sql を完全に取り消す。
-- 依存関係の逆順（子テーブル → 親テーブル）で DROP する。
-- =============================================================================

BEGIN;

-- インデックスは DROP TABLE で自動削除されるため個別 DROP 不要

-- 子テーブルから順にDROP（外部キー制約の依存関係に従う）
DROP TABLE IF EXISTS detection_events   CASCADE;
DROP TABLE IF EXISTS location_history   CASCADE;
DROP TABLE IF EXISTS devices            CASCADE;
DROP TABLE IF EXISTS users              CASCADE;

-- トリガー関数
DROP FUNCTION IF EXISTS set_updated_at() CASCADE;

COMMIT;
