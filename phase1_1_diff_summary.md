# Phase 1.1 修正差分サマリー
## 2026-03-03 — event_id UUID v7 + 冪等性 + ACK設計

---

## 修正ファイル一覧

| ファイル | 変更内容 |
|---------|---------|
| `models.py` | DetectionEvent PK: int→UUID, +payload_json, +received_at, +EventDelivery |
| `schemas.py` | DetectionEventRequest: +event_id, +device_id, +detection構造体, ACKレスポンス |
| `event_router.py` | receive_event(): db.add()→INSERT ON CONFLICT RETURNING, 200/201分岐 |
| `migration_phase1_1.py` | Alembicマイグレーション（手動記述版） |

---

## models.py 変更点

### DetectionEvent
```
- id          = Column(Integer, primary_key=True, autoincrement=True)
+ event_id    = Column(UUID(as_uuid=True), primary_key=True)  # デバイス生成UUID v7
+ event_type  = Column(String(32), server_default="detection")
+ occurred_at = Column(DateTime(timezone=True))                # 旧 detected_at
+ received_at = Column(DateTime(timezone=True))                # サーバ受信時刻
+ payload_json = Column(JSONB)                                 # 生ペイロード保存
```
- 既存カラム（detection_type, confidence, ip_address, location_mismatch等）は維持
- ダッシュボード互換性を確保

### EventDelivery（新規テーブル）
```
event_id       UUID PK FK → detection_events.event_id
state          VARCHAR(16) DEFAULT 'received'
retry_count    INTEGER DEFAULT 0
last_attempt_at TIMESTAMPTZ
acked_at       TIMESTAMPTZ
```

---

## schemas.py 変更点

### DetectionEventRequest
```diff
+ event_id:       uuid.UUID       # UUID v7（必須）
+ device_id:      str             # デバイスID（必須）
+ event_type:     str = "detection"
+ occurred_at:    datetime        # 検知時刻（必須）
+ detection:      DetectionPayload | None  # 構造化検知データ
+ gps:            GpsData | None
+ device_status:  DeviceStatus | None
  detection_type: str | None      # 後方互換（移行期間中）
  confidence:     float | None    # 後方互換
  image_base64:   str | None
  timestamp:      datetime | None # 後方互換
```

### DetectionEventResponse
```diff
- event_id: int
- location_mismatch: bool
+ event_id: str           # UUID文字列
+ status: str             # "accepted" or "duplicate"
+ location_mismatch: bool
```

---

## event_router.py 変更点

### receive_event() — コア変更

**旧:**
```python
event = DetectionEvent(...)
db.add(event)
await db.flush()
# ... geolocation ...
await db.commit()
return DetectionEventResponse(event_id=event.id, location_mismatch=mismatch)
```

**新:**
```python
# 冪等INSERT
stmt = (
    insert(DetectionEvent)
    .values(event_id=body.event_id, ...)
    .on_conflict_do_nothing(index_elements=["event_id"])
    .returning(DetectionEvent.event_id)
)
result = await db.execute(stmt)
inserted_row = result.fetchone()

if inserted_row is None:
    # 重複 → 200 OK
    return JSONResponse(status_code=200, content={"status": "duplicate", ...})

# 新規 → event_delivery作成 + geolocation + 201 Created
```

### その他の変更
- suspended デバイスのHTTPステータス: 503 → **403** に変更
  - 理由: 503はサーバ一時障害を意味する。デバイス停止は認可拒否（403）が正しい
  - デバイス側: 403は永続エラー → リトライしない → P2（キュー蓄積）防止
- X-Retry-Count ヘッダ読み取り → event_delivery.retry_count に記録
- ログ出力強化（event_id, device_id, retry_count）

---

## Claude Code解析 問題点の対応状況

| # | 問題 | 対応 | ファイル |
|---|------|------|---------|
| P1 | リトライ空振り | **Phase 1.2 lte_sender.py修正で対応** | — |
| P2 | 4xxキュー蓄積 | ✅ 403(suspended), 400(mismatch) → デバイス側で棄却 | event_router.py |
| P3 | ステータスコード不一致 | ✅ 201(新規)/200(重複) + デバイス側[200,201]判定 | event_router.py |
| P4 | Secondary消失 | **Dual Reporting廃止で解決（lte_sender.py修正）** | — |
| P5 | 冪等性欠如 | ✅ ON CONFLICT DO NOTHING RETURNING | event_router.py |
| P7 | シークレットHC | **Phase 2で対応** | — |

---

## 適用手順

```bash
# 1. Windowsでファイルをコピー
cp models.py      C:\Users\manta\leonardo-jr-api\leonardo_api\models.py
cp schemas.py     C:\Users\manta\leonardo-jr-api\leonardo_api\schemas.py
cp event_router.py C:\Users\manta\leonardo-jr-api\leonardo_api\routers\event_router.py

# 2. Alembicマイグレーション生成
cd C:\Users\manta\leonardo-jr-api
alembic revision --autogenerate -m "phase1_1_uuid_event_id"

# 3. 生成されたマイグレーションを確認
#    → autogenerate が PK変更を検出しない場合は migration_phase1_1.py の内容を手動で記述

# 4. マイグレーション実行
alembic upgrade head

# 5. ローカルテスト
#    → curl or httpie で POST /api/v1/devices/{device_id}/event を叩く
#    → 同じ event_id で2回送信 → 1回目 201, 2回目 200 を確認
```

---

## 次のタスク: Phase 1.2 lte_sender.py 修正

event_router.py の修正が動作確認できたら、デバイス側:
1. UUID v7 生成（`uuid6` or `uuid_utils` パッケージ）
2. 新スキーマでのPOST送信
3. ACK判定ロジック（200/201=成功, 4xx=棄却, 5xx=リトライ）
4. 指数バックオフ実装
5. Dual Reporting廃止
