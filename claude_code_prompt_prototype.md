# Claude Code 実装プロンプト：Leonardo Jr. 実証機用 QR初期設定＋軽量防御

## コンテキスト

あなたはLeonardo Jr.の実証機向けQR初期設定機能を実装するエンジニアです。
Leonardo Jr.は熱画像カメラベースのAI野生動物検知システム（エッジAI警戒装置）で、
Raspberry Pi 5 + Hailo-8L + LTEモジュール（SIMCOM SIM7600）で構成されています。

**重要：これは実証機（ALSOK・自治体向けデモ機）の実装です。**
量産機向けの重い防御設計（チャレンジ・レスポンス、device_tokens分離、Secure Boot等）は実装しません。
添付の「実証機用設計定義書（prototype_design_spec.md）」を仕様として使用してください。

---

## 設計原則

1. **シンプルに動くこと最優先** — 完璧なセキュリティより、デモで確実に動作することが重要
2. **QR読み取り→設定完了が現場で5分以内** — UXが最重要
3. **量産機への拡張パスを残す** — テーブル構造やAPI設計は量産機v1.2に拡張可能な形で設計
4. **同一トランザクション** — location登録時のactive_flag制御は同一トランザクション内
5. **実証機では認証を軽く** — SMS OTPは省略、メール+パスワードのJWT認証のみ

---

## Phase 1：デバイス側 — device_id生成とQRコード出力

### 1.1 device_id生成スクリプト

```python
# /opt/leonardo/scripts/generate_device_id.py
#
# 処理：
# 1. /etc/leonardo/device_id が存在すれば読み込んで終了
# 2. 存在しなければ以下で生成：
#    - CPUシリアル取得: /proc/cpuinfo の Serial行の下8桁
#    - 4桁乱数生成
#    - device_id = f"LJ-{cpu_serial_last8}-{random4}"
#    - /etc/leonardo/device_id に保存
# 3. device_idを標準出力に返す
#
# 依存: なし（標準ライブラリのみ）
```

### 1.2 factory_token生成

```python
# device_idから固定シークレットでfactory_tokenを導出
#
# factory_secret = "LEONARDO_JR_2026_SECRET"
# factory_token = sha256(f"{device_id}:{factory_secret}")[:16]  ← デバイス内部保持のみ
# factory_token_hash = sha256(factory_token)[:16]               ← QRに埋め込む値
#
# QRのURLパラメータには factory_token_hash（fth）のみ含める
# factory_token自体はURLに出さない（ブラウザ履歴・リファラ・ログに残るため）
# サーバ側にはfactory_token_hashを保存し、QRのfthパラメータと照合
#
# 注意：実証機用の固定シークレット。量産機ではワンタイムチャレンジに移行。
```

### 1.3 QRコード生成

```python
# /opt/leonardo/scripts/generate_qr.py
#
# 処理：
# 1. device_id読み込み
# 2. factory_token生成 → factory_token_hash生成
# 3. URL組み立て: https://setup.leonardo-jr.jp/register?device_id={device_id}&fth={factory_token_hash}
#    ※ factory_token自体はURLに含めない（平文漏洩防止）
# 4. QRコード画像生成（qrcodeライブラリ使用）
# 5. /etc/leonardo/qr_setup.png に保存
# 6. コンソールにASCII QRも出力（開発用）
#
# 依存: pip install qrcode[pil]
```

### 1.4 初回起動時の自動実行

```python
# /opt/leonardo/scripts/init_device.py
#
# 起動時に呼ばれる初期化スクリプト：
# 1. device_id生成（未生成なら）
# 2. QRコード生成（未生成なら）
# 3. device_idとQRのパスをログ出力
#
# 注意：サーバへのpre-registerは行わない。
# devicesレコードはユーザーがQR登録した時点でサーバ側に作成される。
# これにより起動時のLTE通信を削減し、圏外での初回起動にも対応。
#
# systemdサービスとして登録: leonardo-init.service
```

---

## Phase 2：データベーススキーマ

PostgreSQLで以下のテーブルを作成してください。
**量産機v1.2への拡張を考慮し、カラムは余裕を持たせる。ただしENUMは使わずVARCHAR + CHECK制約で。**

### 2.1 テーブル定義

```sql
-- 1. users
CREATE TABLE users (
    user_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) UNIQUE NOT NULL,
    phone_number VARCHAR(20),                    -- 実証機ではSMS認証なし、通知用
    password_hash VARCHAR(255) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- 2. devices
CREATE TABLE devices (
    device_id VARCHAR(30) PRIMARY KEY,           -- 例: LJ-A3F8B2C1-7294
    cpu_serial VARCHAR(20),                      -- Raspberry PiのCPUシリアル
    owner_user_id UUID REFERENCES users(user_id),-- NULLなら未登録
    factory_token_hash VARCHAR(64) NOT NULL,     -- factory_tokenのハッシュ
    api_token VARCHAR(255),                      -- 実証機では単一トークン（量産機でテーブル分離）
    token_expires_at TIMESTAMPTZ,
    status VARCHAR(20) NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'suspended')),  -- 実証機ではstolenは省略
    plan_type VARCHAR(20) NOT NULL DEFAULT 'consumer'
        CHECK (plan_type IN ('consumer', 'ultimate')),
    notification_target VARCHAR(500),            -- LINE/メール等の通知先JSON
    detection_targets VARCHAR(500),              -- 検知対象JSON（bear, human, vehicle等）
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- 3. location_history
CREATE TABLE location_history (
    id SERIAL PRIMARY KEY,
    device_id VARCHAR(30) NOT NULL REFERENCES devices(device_id),
    lat DECIMAL(10, 8) NOT NULL,
    lon DECIMAL(11, 8) NOT NULL,
    accuracy DECIMAL(8, 2),                      -- NULL許可、CHECK制約なし
    registered_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    registered_by UUID NOT NULL REFERENCES users(user_id),
    active_flag BOOLEAN DEFAULT TRUE,
    ip_address INET
);
CREATE INDEX idx_location_device_time ON location_history(device_id, registered_at DESC);

-- 4. detection_events（検知イベントログ）
CREATE TABLE detection_events (
    id SERIAL PRIMARY KEY,
    device_id VARCHAR(30) NOT NULL REFERENCES devices(device_id),
    detected_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    detection_type VARCHAR(20),                  -- bear, human, vehicle等
    confidence DECIMAL(5, 4),                    -- AI信頼度
    image_path VARCHAR(500),                     -- 検知画像パス
    ip_address INET,                             -- 発報時IP
    ip_geolocation_region VARCHAR(100),           -- IPジオロケーション結果
    distance_from_registered_km DECIMAL,          -- 登録座標との距離
    location_mismatch BOOLEAN DEFAULT FALSE       -- 150km以上乖離 or 都道府県不一致ならTRUE
);
CREATE INDEX idx_detection_device_time ON detection_events(device_id, detected_at DESC);
```

出力：上記SQLのマイグレーションファイル

---

## Phase 3：バックエンドAPI（FastAPI）

### 3.1 プロジェクト構造

```
leonardo_api/
├── main.py                  # FastAPIアプリ
├── config.py                # 設定（DB接続、シークレット等）
├── models.py                # SQLAlchemy モデル
├── schemas.py               # Pydantic スキーマ
├── auth.py                  # JWT認証
├── routers/
│   ├── auth_router.py       # 認証系API
│   ├── device_router.py     # デバイス登録系API
│   ├── location_router.py   # 位置登録系API
│   └── event_router.py      # 検知イベント受信API
├── services/
│   ├── device_service.py    # デバイス登録ロジック
│   ├── location_service.py  # 位置登録ロジック
│   ├── geolocation_service.py # IPジオロケーション
│   └── notification_service.py # 通知送信
└── requirements.txt
```

### 3.2 エンドポイント

#### 認証系

```
POST /api/v1/auth/register
  - email, password, phone_number（任意）でアカウント作成
  - パスワードはbcryptハッシュ
  - JWT発行

POST /api/v1/auth/login
  - email + password → JWT発行

POST /api/v1/auth/refresh
  - JWTリフレッシュ
```

#### デバイス登録（ユーザーがQRから呼ぶ）

```
POST /api/v1/devices/{device_id}/register
  - JWT認証必須
  - Query: fth（factory_token_hash）
  - 処理：
    1. factory_token_hashの検証：
       - factory_secret + device_idからfactory_tokenを再導出
       - factory_tokenをsha256してfthパラメータと照合
    2. devicesテーブルにdevice_idが存在するか確認
       - 存在しない → devicesレコードをこの時点で作成（owner未設定）
       - 存在＋owner_user_id IS NULL → 登録可能
       - 存在＋owner設定済み → 409 Conflict
    3. owner_user_idにログインユーザーを設定
    4. APIトークン発行（デバイス通信用）
    5. レスポンス：{ device_id, api_token, message: "登録完了" }
  - 注意：pre-register APIは不要。このエンドポイントでdevice作成も行う。
```

#### セットアップ

```
PUT /api/v1/devices/{device_id}/setup
  - JWT認証＋所有者確認
  - Body: { notification_target, detection_targets }
  - devicesテーブル更新
```

#### 位置登録

```
POST /api/v1/devices/{device_id}/location
  - JWT認証＋所有者確認
  - Body: { lat, lon, accuracy }
  - 処理：
    1. accuracyチェック（100m超は警告をレスポンスに含む、拒否はしない）
    2. 【トランザクション】既存active_flag = FALSE → 新規レコード挿入（active_flag = TRUE）
    3. リクエスト元IPを保存
  - レスポンス：{ location_id, warning?（accuracyが50m超の場合） }
```

#### 再設置

```
POST /api/v1/devices/{device_id}/relocate
  - JWT認証＋所有者確認
  - パスワード再入力必須（Body: { password, lat, lon, accuracy }）
  - パスワード照合後、位置登録と同じフローを実行
  - 旧座標は履歴保存
```

#### 検知イベント受信

```
POST /api/v1/devices/{device_id}/event
  - APIトークン認証（デバイスから送信）
  - Body: { detection_type, confidence, image_base64?, timestamp }
  - 処理：
    1. デバイスstatus確認（suspended → 拒否）
    2. detection_eventsにレコード挿入
    3. リクエスト元IPからジオロケーション取得
    4. 登録座標との距離概算
    5. 150km以上乖離 or 都道府県不一致 → location_mismatch = TRUE → 所有者に通知
    6. 通常の検知通知を所有者に送信
  - レスポンス：{ event_id, location_mismatch }
```

#### デバイスステータス確認

```
GET /api/v1/devices/{device_id}/status
  - APIトークン認証
  - レスポンス：{ status, active_location }
  - デバイスが圏内復帰時に呼ぶ
```

#### ログ一括アップロード

```
POST /api/v1/devices/{device_id}/upload-logs
  - APIトークン認証
  - Body: { events: [ { detection_type, confidence, timestamp, ... }, ... ] }
  - 圏外中に溜まったJSONLログを一括でdetection_eventsに挿入
```

### 3.3 IPジオロケーション

```python
# services/geolocation_service.py
#
# 実証機では無料のIPジオロケーションAPIを使用：
# - ip-api.com（無料、商用利用は要確認）
# - または ipinfo.io（月50kリクエスト無料）
#
# レスポンスから緯度経度を取得し、登録座標とのHaversine距離を計算
# 150km以上 または 都道府県が異なる場合 → location_mismatch = TRUE
#
# 注意：LTE IPジオロケーションは数十km単位でズレることがあり、
# キャリアNATで遠隔地域IPが割り当てられるケースもある。
# 実証機ではデモ中の誤検知を避けるため閾値を150kmに設定。
#
# 量産機ではMaxMind GeoIP2ローカルDB等に移行
```

### 3.4 通知

```python
# services/notification_service.py
#
# 実証機では以下をサポート：
# - LINE Notify（既存の発報連携を流用）
# - メール（SMTP）
#
# 通知タイミング：
# - 検知イベント発生時
# - 位置逸脱検知時（100km以上乖離）
```

---

## Phase 4：Webセットアップ画面（フロントエンド）

### 4.1 概要

QR読み取り後にスマホブラウザで開くセットアップ画面。
シングルページで完結するレスポンシブWeb。

### 4.2 技術スタック

- HTML + JavaScript（フレームワーク不要、Vanilla JS）
- Tailwind CSS（CDN）
- Geolocation API（スマホGPS取得）

### 4.3 画面遷移

```
1. [ログイン / 新規登録画面]
   - メール + パスワード
   - 新規ユーザーはここでアカウント作成

2. [デバイス登録確認画面]
   - device_id表示
   - 「このデバイスを登録しますか？」

3. [セットアップ画面]
   - 通知先設定（LINE Notifyトークン / メールアドレス）
   - 検知対象選択（チェックボックス：熊 / 人 / 車両）

4. [位置登録画面]
   - 地図表示（Leaflet.js + OpenStreetMap）
   - 「現在地を取得」ボタン
   - accuracy表示
   - 100m超の場合は警告メッセージ
   - 「この位置で登録」ボタン

5. [完了画面]
   - 「設定完了！」
   - ダッシュボードへのリンク
```

### 4.4 GPS取得

```javascript
// navigator.geolocation.getCurrentPosition を使用
// options: { enableHighAccuracy: true, timeout: 30000 }
// accuracy値を画面に表示
// 50m以下: 緑、50-100m: 黄、100m超: 赤で表示
```

---

## 実装順序

1. **デバイス側スクリプト** — device_id生成 → factory_token生成 → QRコード生成 → init_device.py
2. **DBスキーマ** — マイグレーション実行
3. **バックエンドAPI** — auth → register（device作成含む）→ setup → location → relocate → event → status → upload-logs
4. **IPジオロケーション** — 距離計算 → 逸脱アラート
5. **Webセットアップ画面** — ログイン → 登録確認 → セットアップ → GPS取得 → 完了
6. **結合テスト** — QR読み取り→登録→検知イベント→通知の一気通貫テスト

---

## 出力形式

各Phaseごとに以下を出力してください：

1. 実装コード（コメント付き）
2. 簡潔な設計判断の説明
3. テストコード（pytest / デバイス側はスクリプト単体テスト）

Phase 1（デバイス側スクリプト）から開始してください。

---

## 注意事項

- 実証機スコープを守ること。量産機の重い設計は実装しない
- device_idはCPUシリアル＋乱数で生成
- factory_tokenは固定シークレットから導出（実証機用）
- **factory_token自体はURLに含めない。QRにはfactory_token_hashのみ埋め込み、サーバ側で再ハッシュ照合**
- **pre-register APIは不要。QR登録時にdevicesレコードが存在しなければその場で作成**
- owner_user_id IS NULLまたはdevices未存在のときのみ初回登録許可
- accuracyは警告のみ、拒否しない（山間部テスト対応）
- **位置逸脱は150km or 都道府県不一致で判定（LTE IPジオロケーションのズレ考慮）**
- 位置逸脱は通知のみ、自動ロックはしない
- active_flag制御は同一トランザクション内
- 既存のLeonardo Jr.ファームウェア（/opt/leonardo/）との統合を意識すること
- 既存のLINE Notify連携を通知に流用すること
