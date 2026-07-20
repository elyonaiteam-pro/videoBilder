"""
assets/library.py — реестр локальных материалов New Era.

Единственный источник ассетов для роликов: всё лежит в settings.new_assets_dir
(по умолчанию new_main_assets/), никаких внешних стоков (Pexels/Pixabay) —
см. TODO в config/settings.py про их удаление в конце рефакторинга.

Структура new_main_assets/ (см. plans_for_videobilder.txt):
  backgroundvideos/{dark,light}background/{N}.mp4           — сами фоны
  select_background_for_videos_in_bot/{dark,light}background/{N}.jpg — превью для выбора в боте
  mainstikers/<PackName>/{N}.tgs                             — исходные Telegram-стикеры
  stickers_webp/<PackName>/{N}.webp                          — первая конвертация (анимированный
                                                                WebP с альфа-каналом,
                                                                scripts/convert_stickers.py) —
                                                                оставлена как референс, рендером
                                                                не используется
  validstikers/<PackName>/{N}.gif                            — ФИНАЛЬНЫЙ формат для рендера:
                                                                GIF с прозрачностью, вручную
                                                                проверен и пересобран из webp;
                                                                ffmpeg работает с GIF-оверлеями
                                                                предсказуемее, чем с animated WebP
  banners_videos_for_end/banner{N}.mp4                       — концовки-баннеры
  songs_for_videos/*.mp3|*.m4a                                — фоновая музыка
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from config.settings import Settings

BackgroundTheme = Literal["dark", "light"]

_THEME_DIR = {"dark": "darkbackground", "light": "lightbackground"}

_VIDEO_EXTS = {".mp3", ".m4a", ".wav", ".ogg"}


@dataclass(frozen=True)
class BackgroundOption:
    number: int
    theme: BackgroundTheme
    preview_path: Path  # jpg для показа в боте при выборе
    video_path: Path    # mp4, реально идёт в рендер


@dataclass(frozen=True)
class StickerPack:
    name: str
    stickers: list[Path]  # готовые .gif с прозрачностью, отсортированы по номеру


class AssetLibrary:
    """Индексирует new_main_assets/ один раз при старте и отдаёт готовые списки/пути.

    Ничего не скачивает и не генерирует — если ассета нет на диске, значит
    его нет и в боте (нет скрытых сетевых fallback-ов, как раньше с Pexels).
    """

    def __init__(self, settings: Settings) -> None:
        self.root = settings.new_assets_dir
        if not self.root.is_absolute():
            self.root = Path.cwd() / self.root

    # ---------- фоны ----------

    def list_backgrounds(self, theme: BackgroundTheme) -> list[BackgroundOption]:
        preview_dir = self.root / "select_background_for_videos_in_bot" / _THEME_DIR[theme]
        video_dir = self.root / "backgroundvideos" / _THEME_DIR[theme]
        options: list[BackgroundOption] = []
        if not preview_dir.is_dir():
            return options
        for preview in sorted(preview_dir.glob("*.jpg"), key=lambda p: _numeric_key(p.stem)):
            number = _numeric_key(preview.stem)
            video = video_dir / f"{preview.stem}.mp4"
            if video.exists():
                options.append(
                    BackgroundOption(number=number, theme=theme, preview_path=preview, video_path=video)
                )
        return options

    def get_background_video(self, theme: BackgroundTheme, number: int) -> Path | None:
        video = self.root / "backgroundvideos" / _THEME_DIR[theme] / f"{number}.mp4"
        return video if video.exists() else None

    # ---------- стикеры ----------

    def list_sticker_packs(self) -> list[StickerPack]:
        packs_dir = self.root / "validstikers"
        packs: list[StickerPack] = []
        if not packs_dir.is_dir():
            return packs
        for pack_dir in sorted(packs_dir.iterdir()):
            if not pack_dir.is_dir():
                continue
            stickers = sorted(pack_dir.glob("*.gif"), key=lambda p: _numeric_key(p.stem))
            if stickers:
                packs.append(StickerPack(name=pack_dir.name, stickers=stickers))
        return packs

    def get_sticker_pack(self, name: str) -> StickerPack | None:
        for pack in self.list_sticker_packs():
            if pack.name == name:
                return pack
        return None

    # ---------- баннеры-концовки ----------

    def list_banners(self) -> list[Path]:
        banners_dir = self.root / "banners_videos_for_end"
        if not banners_dir.is_dir():
            return []
        return sorted(banners_dir.glob("*.mp4"), key=lambda p: _numeric_key(p.stem))

    # ---------- музыка ----------

    def list_songs(self) -> list[Path]:
        songs_dir = self.root / "songs_for_videos"
        if not songs_dir.is_dir():
            return []
        return sorted(
            p for p in songs_dir.iterdir() if p.is_file() and p.suffix.lower() in _VIDEO_EXTS
        )


def _numeric_key(stem: str) -> int:
    """Извлекает число из имени файла ('banner4' -> 4, '12' -> 12) для сортировки по номеру,
    а не по алфавиту (иначе '10' встаёт перед '2')."""
    digits = "".join(ch for ch in stem if ch.isdigit())
    return int(digits) if digits else 0
