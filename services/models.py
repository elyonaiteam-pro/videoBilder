from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

IdeaSource = Literal["template", "custom", "gemini"]
BackgroundTheme = Literal["dark", "light"]


class Scene(BaseModel):
    """
    New Era: сцена больше не тянет визуал из внешних стоков (не нужны
    visual_prompt/asset_keywords для Pexels) — единственный визуал ролика
    это один зацикленный локальный фон + стикеры поверх, которые подбирает
    Gemini из уже выбранного пака (см. StickerPlacement).
    """

    index: int
    title: str
    duration: float = Field(ge=0.5, le=8.0)
    voiceover: str
    on_screen_text: str


class VideoScript(BaseModel):
    title: str
    template_id: str
    hook: str
    script: str
    voiceover: str
    on_screen_texts: list[str]
    publication_description: str
    hashtags: list[str]
    scenes: list[Scene]


class StickerPlacement(BaseModel):
    """Один стикер и в какую сцену ролика он ставится (подбирает Gemini)."""

    sticker_index: int  # индекс в StickerPack.stickers выбранного пака
    scene_index: int    # к какой Scene.index он привязан по времени


class GenerationRequest(BaseModel):
    """
    Полный набор данных, собранный FSM-диалогом бота (см. bot/states.py),
    прежде чем передать всё в рендер (video_generation/pipeline.py).

    Сборка ffmpeg под эти поля — Фаза 4 ("ffmpeg-сборка"), пока не реализована.
    """

    idea_source: IdeaSource
    idea_text: str  # шаблонная идея (angle) / текст пользователя / идея от Gemini

    background_theme: BackgroundTheme
    background_number: int
    background_path: Path

    sticker_pack_name: str
    sticker_placements: list[StickerPlacement]

    song_path: Path
    banner_path: Path

    script: VideoScript


class GenerationResult(BaseModel):
    request: GenerationRequest
    # script продублирован отдельным полем (не @property) — HistoryRepository
    # читает payload["script"] напрямую из сериализованного JSON при восстановлении
    # used_scripts(), а pydantic не сериализует @property в model_dump_json().
    script: VideoScript
    video_path: Path | None = None  # None пока Фаза 4 (рендер) не реализована
    subtitle_path: Path | None = None
    voice_path: Path | None = None
