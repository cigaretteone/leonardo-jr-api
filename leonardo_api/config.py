"""
config.py — アプリケーション設定

環境変数 or .env ファイルから読み込む。
本番では .env をリポジトリに含めず、環境変数で注入すること。
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # -------------------------------------------------------------------------
    # データベース
    # -------------------------------------------------------------------------
    DATABASE_URL: str = (
        "postgresql+asyncpg://postgres:password@localhost:5432/leonardo_jr_dev"
    )

    # -------------------------------------------------------------------------
    # JWT 認証（ユーザー向け）
    # -------------------------------------------------------------------------
    JWT_SECRET_KEY: str = "change-me-in-production-use-long-random-string"
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 30

    # -------------------------------------------------------------------------
    # デバイス認証
    # factory_token 導出に使う固定シークレット（実証機用）
    # 量産機ではワンタイムチャレンジ方式に移行（v1.2）
    # -------------------------------------------------------------------------
    FACTORY_SECRET: str = "LEONARDO_JR_2026_SECRET"

    # -------------------------------------------------------------------------
    # 通知：LINE Notify
    # デバイス個別の LINE トークンは devices.notification_target に保存する。
    # ここはシステム全体の管理者向けフォールバック用。
    # -------------------------------------------------------------------------
    LINE_NOTIFY_API_URL: str = "https://notify-api.line.me/api/notify"

    # -------------------------------------------------------------------------
    # 通知：SMTP（メール）
    # -------------------------------------------------------------------------
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = "noreply@leonardo-jr.jp"

    # -------------------------------------------------------------------------
    # IPジオロケーション
    # 実証機では ip-api.com（無料、45req/min）を使用。
    # 量産機では MaxMind GeoIP2 ローカル DB に移行。
    # -------------------------------------------------------------------------
    GEOLOCATION_API_URL: str = "http://ip-api.com/json"

    # 位置逸脱判定閾値（km）
    # LTE の IP ジオロケーションは数十 km 単位でズレるため、150km に設定。
    LOCATION_MISMATCH_THRESHOLD_KM: float = 150.0

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # .env に未知のキーがあっても無視
    )


settings = Settings()
