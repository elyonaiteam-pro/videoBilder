from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_ASPECT_RATIO = (9, 16)
MaleVoice = Literal["ru-RU-DmitryNeural"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    allowed_user_ids: str = Field(default="", alias="ALLOWED_USER_IDS")
    gemini_api_key: str = Field(default="", alias="GEMINI_API_KEY")
    gemini_model: str = Field(default="gemini-3-pro-preview", alias="GEMINI_MODEL")
    llm_temperature: float = Field(default=0.9, alias="LLM_TEMPERATURE")

    app_name: str = Field(default="AtlantaVPN Video Bot", alias="APP_NAME")
    brand_name: str = Field(default="AtlantaVPN", alias="BRAND_NAME")
    brand_url: str = Field(default="https://atlantavpn.example", alias="BRAND_URL")
    cta_text: str = Field(default="Ссылка в шапке профиля", alias="CTA_TEXT")

    video_width: int = Field(default=720, alias="VIDEO_WIDTH", gt=0)
    video_height: int = Field(default=1280, alias="VIDEO_HEIGHT", gt=0)
    min_duration_seconds: int = Field(default=15, alias="MIN_DURATION_SECONDS")
    max_duration_seconds: int = Field(default=25, alias="MAX_DURATION_SECONDS")
    fps: int = Field(default=30, alias="FPS")

    default_tts_engine: str = Field(default="elevenlabs", alias="DEFAULT_TTS_ENGINE")
    default_tts_voice: MaleVoice = Field(default="ru-RU-DmitryNeural", alias="DEFAULT_TTS_VOICE")
    default_voice_gender: Literal["male"] = Field(default="male", alias="DEFAULT_VOICE_GENDER")
    speech_rate: str = Field(default="+5%", alias="SPEECH_RATE")
    speech_pitch: str = Field(default="+0Hz", alias="SPEECH_PITCH")

    elevenlabs_api_key: str = Field(default="", alias="ELEVENLABS_API_KEY")
    elevenlabs_voice_id_dmitriy: str = Field(default="", alias="ELEVENLABS_VOICE_ID_DMITRIY")
    elevenlabs_voice_id_adam: str = Field(default="pNInz6obpgDQGcFmaJgB", alias="ELEVENLABS_VOICE_ID_ADAM")
    elevenlabs_model: str = Field(default="eleven_multilingual_v2", alias="ELEVENLABS_MODEL")

    cache_dir: Path = Field(default=Path("cache"), alias="CACHE_DIR")
    logs_dir: Path = Field(default=Path("logs"), alias="LOGS_DIR")
    assets_dir: Path = Field(default=Path("assets"), alias="ASSETS_DIR")
    templates_dir: Path = Field(default=Path("templates"), alias="TEMPLATES_DIR")
    sqlite_path: Path = Field(default=Path("cache/bot.sqlite3"), alias="SQLITE_PATH")

    new_assets_dir: Path = Field(default=Path("new_main_assets"), alias="NEW_ASSETS_DIR")

    # ── Деплой: вебхук + (опционально) прокси Telegram API + Google Drive ──
    tg_proxy_url: str = Field(default="", alias="TG_PROXY_URL")
    public_base_url: str = Field(default="", alias="PUBLIC_BASE_URL")
    webhook_secret: str = Field(default="", alias="WEBHOOK_SECRET")
    port: int = Field(default=8080, alias="PORT")

    google_drive_folder_id: str = Field(default="", alias="GOOGLE_DRIVE_FOLDER_ID")
    google_service_account_json: str = Field(default="", alias="GOOGLE_SERVICE_ACCOUNT_JSON")

    @field_validator("video_width", "video_height")
    @classmethod
    def _must_be_multiple_of_two(cls, v: int) -> int:
        if v % 2 != 0:
            raise ValueError(f"video dimension must be even for libx264, got {v}")
        return v

    @model_validator(mode="after")
    def _enforce_9_16_aspect_ratio(self) -> "Settings":
        w_ratio, h_ratio = _ASPECT_RATIO
        if self.video_width * h_ratio != self.video_height * w_ratio:
            raise ValueError(
                f"video_width={self.video_width} x video_height={self.video_height} "
                f"is not a {w_ratio}:{h_ratio} ratio — required for TikTok/Reels/Shorts. "
                f"Use e.g. 720x1280 or 1080x1920."
            )
        return self

    @property
    def allowed_users(self) -> set[int]:
        if not self.allowed_user_ids.strip():
            return set()
        return {int(item.strip()) for item in self.allowed_user_ids.split(",") if item.strip()}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.cache_dir.mkdir(parents=True, exist_ok=True)
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    settings.assets_dir.mkdir(parents=True, exist_ok=True)
    (settings.cache_dir / "videos").mkdir(parents=True, exist_ok=True)
    (settings.cache_dir / "audio").mkdir(parents=True, exist_ok=True)
    return settings
