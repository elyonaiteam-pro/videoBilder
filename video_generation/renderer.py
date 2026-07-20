"""
video_generation/renderer.py — Фаза 4: финальная ffmpeg-сборка ролика.

RAM-ЭКОНОМНАЯ ПОЭТАПНАЯ СХЕМА (важно для Render free tier, 512 МБ RAM!):
  Проверено эмпирически: один большой filter_complex, держащий открытыми
  сразу фон+все голосовые дорожки+музыку+все стикеры+баннер, потребляет
  почти в 2 раза больше пиковой памяти, чем та же работа, разбитая на
  маленькие последовательные шаги (94 МБ против 171 МБ на тестовых клипах).
  Плюс VP9 (libvpx-vp9) для стикеров с альфой заметно прожорливее, чем
  палитровый GIF (116 МБ против 90 МБ) — и GIF всё равно наш основной
  формат стикеров (validstikers/*.gif), поэтому используем именно его.

  Поэтому рендер идёт в 5 маленьких шагов вместо одного большого:
    A) main_video.mp4  — фон + подписи по сценам + стикеры, ТОЛЬКО видео
    B) main_audio.m4a  — голос по сценам (конкатенация с паузами) + тихая
                          музыка, ТОЛЬКО аудио (аудио — дёшево по RAM)
    C) main_combined   — мукс A+B БЕЗ реэнкода (-c copy, почти бесплатно)
    D) banner_prepared — баннер, кодируется С ТЕМИ ЖЕ параметрами кодека,
                          что и main_combined (тот же профиль/pix_fmt/fps/
                          аудио-формат) — специально для шага E
    E) финальный файл  — конкатенация C + D через concat DEMUXER (не
                          filter!) с -c copy — тоже почти бесплатно,
                          т.к. кодеки уже совпадают

  Дополнительно: `-stream_loop` внутри сложного filter_complex ловит
  реальный баг ffmpeg (PTS "уезжает" на стыке повторов, downstream-фильтры
  массово дублируют кадры — проверено: 5.8-секундный ролик разрастался до
  180+ секунд). Поэтому зацикливаемые материалы (фон короче ролика, стикер
  короче своей сцены) готовятся ОТДЕЛЬНЫМ простым проходом ffmpeg до точной
  нужной длины (`_prepare_looped_clip`) — без каких-либо других фильтров
  в этом проходе, и БЕЗ `-stream_loop` в шагах A-E.

  Везде, где идёт реальное кодирование видео: `-threads 1`, `-preset
  ultrafast`, `-bufsize`/`-maxrate` — тот же рецепт, что и в старом
  рендерере (v9), который был специально настроен под Render free tier.
"""
import asyncio
import logging
import math
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from config.settings import Settings
from services.models import GenerationRequest

logger = logging.getLogger(__name__)

_GAP_BETWEEN_SCENES = 0.3

_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
]

# Общие для видео-кодирования флаги экономии RAM (как в старом рендерере v9,
# настроенном под Render free tier 512 МБ).
_LEAN_VIDEO_FLAGS = [
    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "26",
    "-threads", "1", "-bufsize", "1500k", "-maxrate", "2000k",
    "-profile:v", "baseline", "-level", "3.1",
]
_LEAN_AUDIO_FLAGS = ["-c:a", "aac", "-ar", "44100", "-ac", "2", "-b:a", "128k"]


@dataclass
class _SceneTiming:
    index: int
    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


class RenderError(RuntimeError):
    pass


class FFmpegRenderer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.output_dir = settings.cache_dir / "videos"
        self.tmp_dir = settings.cache_dir / "render_tmp"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        self._font = self._find_font()

    async def render(self, request: GenerationRequest, scene_audio_paths: list[Path]) -> Path:
        scenes = request.script.scenes
        if len(scenes) != len(scene_audio_paths):
            raise RenderError(
                f"scene_audio_paths ({len(scene_audio_paths)}) не совпадает с числом сцен ({len(scenes)})"
            )

        stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        work_dir = self.tmp_dir / stamp
        work_dir.mkdir(parents=True, exist_ok=True)
        try:
            timings = await self._compute_scene_timings(scenes, scene_audio_paths)
            main_duration = timings[-1].end
            W, H, FPS = self.settings.video_width, self.settings.video_height, self.settings.fps

            # ── подготовка зацикленных материалов (отдельным простым проходом) ──
            bg_prepared = await self._prepare_looped_clip(
                source=request.background_path,
                target_duration=main_duration,
                work_dir=work_dir,
                name="bg",
                video_filters=(
                    f"scale={W}:{H}:force_original_aspect_ratio=increase,"
                    f"crop={W}:{H},setsar=1,fps={FPS}"
                ),
                as_gif=False,
            )

            sticker_width = max(120, int(W * 0.39))
            sticker_clips: dict[int, Path] = {}
            for i, placement in enumerate(request.sticker_placements):
                timing = next((t for t in timings if t.index == placement.scene_index), None)
                if timing is None:
                    continue
                sticker_path = self._resolve_sticker_path(request, placement.sticker_index)
                if sticker_path is None:
                    continue
                sticker_clips[i] = await self._prepare_looped_clip(
                    source=sticker_path,
                    target_duration=timing.duration,
                    work_dir=work_dir,
                    name=f"stk{i}",
                    video_filters=f"scale={sticker_width}:-1,fps={FPS}",
                    as_gif=True,
                )

            # ── Stage A: видео (фон + подписи + стикеры), без звука ──
            main_video = await self._stage_main_video(
                request, timings, bg_prepared, sticker_clips, work_dir, W, H, FPS
            )

            # ── Stage B: аудио (голос по сценам + музыка), без видео ──
            main_audio = await self._stage_main_audio(
                request, scene_audio_paths, main_duration, work_dir
            )

            # ── Stage C: мукс видео+аудио БЕЗ реэнкода ──
            main_combined = work_dir / "main_combined.mp4"
            await self._run_ffmpeg([
                "ffmpeg", "-y", "-i", str(main_video), "-i", str(main_audio),
                "-c", "copy", "-shortest", str(main_combined),
            ])

            # ── Stage D: баннер С ТЕМИ ЖЕ параметрами кодека (для copy-конкатенации) ──
            banner_duration = await self._probe_duration(request.banner_path)
            banner_prepared = work_dir / "banner_prepared.mp4"
            await self._run_ffmpeg([
                "ffmpeg", "-y",
                "-i", str(request.banner_path),
                "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
                "-vf", f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,fps={FPS}",
                *_LEAN_VIDEO_FLAGS, *_LEAN_AUDIO_FLAGS,
                "-shortest", "-t", f"{banner_duration:.6f}",
                str(banner_prepared),
            ])

            # ── Stage E: финальная copy-конкатенация через concat demuxer ──
            output_path = self.output_dir / f"video_{stamp}.mp4"
            concat_list = work_dir / "concat_list.txt"
            concat_list.write_text(
                f"file '{main_combined.resolve()}'\nfile '{banner_prepared.resolve()}'\n", encoding="utf-8"
            )
            await self._run_ffmpeg([
                "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list),
                "-c", "copy", "-movflags", "+faststart", str(output_path),
            ])
            return output_path
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    # ── тайминги сцен по РЕАЛЬНОЙ длине озвучки ──

    async def _compute_scene_timings(self, scenes, scene_audio_paths: list[Path]) -> list[_SceneTiming]:
        timings = []
        cursor = 0.0
        for scene, audio_path in zip(scenes, scene_audio_paths, strict=True):
            duration = await self._probe_duration(audio_path)
            start = cursor
            end = cursor + duration
            timings.append(_SceneTiming(index=scene.index, start=start, end=end))
            cursor = end + _GAP_BETWEEN_SCENES
        return timings

    # ── подготовка зацикленных клипов (фон/стикер) отдельным простым проходом ──

    async def _prepare_looped_clip(
        self,
        source: Path,
        target_duration: float,
        work_dir: Path,
        name: str,
        video_filters: str,
        as_gif: bool,
    ) -> Path:
        native_duration = await self._probe_duration(source)
        loop_count = 0
        if native_duration > 0:
            total_plays_needed = math.ceil(target_duration / native_duration)
            loop_count = max(0, total_plays_needed - 1)

        out_path = work_dir / f"{name}.{'gif' if as_gif else 'mp4'}"
        cmd = ["ffmpeg", "-y"]
        if loop_count > 0:
            cmd += ["-stream_loop", str(loop_count)]
        cmd += ["-i", str(source), "-t", f"{target_duration:.6f}"]

        if as_gif:
            # Палитровый GIF — сохраняет альфу (как исходные validstikers/*.gif)
            # и заметно легче по RAM, чем VP9 (проверено: 90 МБ vs 116 МБ).
            filters = f"{video_filters},split[a][b];[a]palettegen=reserve_transparent=1[p];[b][p]paletteuse=alpha_threshold=128"
            cmd += ["-vf", filters, "-threads", "1", "-an", str(out_path)]
        else:
            cmd += ["-vf", video_filters, "-an", *_LEAN_VIDEO_FLAGS, str(out_path)]

        await self._run_ffmpeg(cmd)
        return out_path

    def _resolve_sticker_path(self, request: GenerationRequest, sticker_index: int) -> Path | None:
        from assets.library import AssetLibrary

        library = AssetLibrary(self.settings)
        pack = library.get_sticker_pack(request.sticker_pack_name)
        if pack is None or not (0 <= sticker_index < len(pack.stickers)):
            return None
        return pack.stickers[sticker_index]

    # ── Stage A: видео (фон + подписи + стикеры) ──

    async def _stage_main_video(
        self,
        request: GenerationRequest,
        timings: list[_SceneTiming],
        bg_prepared: Path,
        sticker_clips: dict[int, Path],
        work_dir: Path,
        W: int, H: int, FPS: int,
    ) -> Path:
        fontsize = max(28, int(W * 0.085))
        sticker_y = "H*0.32"

        inputs: list[str] = ["-i", str(bg_prepared)]
        sticker_input_idx: dict[int, int] = {}
        for key, clip in sticker_clips.items():
            sticker_input_idx[key] = len(inputs) // 2
            inputs += ["-i", str(clip)]

        filters: list[str] = ["[0:v]setpts=PTS-STARTPTS[bg0]"]
        prev_label = "bg0"
        for i, (scene, timing) in enumerate(zip(request.script.scenes, timings, strict=True)):
            text = _escape_drawtext(scene.on_screen_text)
            out_label = f"cap{i}"
            font_arg = f"fontfile={self._font}:" if self._font else ""
            filters.append(
                f"[{prev_label}]drawtext={font_arg}text='{text}':fontsize={fontsize}:fontcolor=white:"
                f"borderw=4:bordercolor=black:x=(w-text_w)/2:y=h*0.68:"
                f"enable='between(t,{timing.start:.3f},{timing.end:.3f})'[{out_label}]"
            )
            prev_label = out_label

        for key, clip_input_idx in sticker_input_idx.items():
            placement = request.sticker_placements[key]
            timing = next((t for t in timings if t.index == placement.scene_index), None)
            if timing is None:
                continue
            out_label = f"ov{key}"
            filters.append(
                f"[{clip_input_idx}:v]format=rgba[stk{key}]"
            )
            filters.append(
                f"[{prev_label}][stk{key}]overlay=x=(W-w)/2:y={sticker_y}:"
                f"enable='between(t,{timing.start:.3f},{timing.end:.3f})'[{out_label}]"
            )
            prev_label = out_label

        out_path = work_dir / "main_video.mp4"
        cmd = [
            "ffmpeg", "-y", *inputs,
            "-filter_complex", ";".join(filters),
            "-map", f"[{prev_label}]",
            *_LEAN_VIDEO_FLAGS, "-pix_fmt", "yuv420p", "-r", str(FPS),
            "-an", str(out_path),
        ]
        await self._run_ffmpeg(cmd)
        return out_path

    # ── Stage B: аудио (голос по сценам + музыка) ──

    async def _stage_main_audio(
        self,
        request: GenerationRequest,
        scene_audio_paths: list[Path],
        main_duration: float,
        work_dir: Path,
    ) -> Path:
        inputs: list[str] = []
        for p in scene_audio_paths:
            inputs += ["-i", str(p)]
        idx_song = len(inputs) // 2
        inputs += ["-i", str(request.song_path)]

        filters: list[str] = []
        for i in range(len(scene_audio_paths)):
            label = f"vraw{i}"
            if i < len(scene_audio_paths) - 1:
                filters.append(f"[{i}:a]apad=pad_dur={_GAP_BETWEEN_SCENES}[{label}]")
            else:
                filters.append(f"[{i}:a]anull[{label}]")
        concat_inputs = "".join(f"[vraw{i}]" for i in range(len(scene_audio_paths)))
        filters.append(f"{concat_inputs}concat=n={len(scene_audio_paths)}:v=0:a=1[voice]")
        filters.append(
            f"[{idx_song}:a]atrim=duration={main_duration:.6f},asetpts=PTS-STARTPTS,volume=0.15[music]"
        )
        filters.append("[voice][music]amix=inputs=2:duration=first:dropout_transition=2[aout]")

        out_path = work_dir / "main_audio.m4a"
        cmd = [
            "ffmpeg", "-y", *inputs,
            "-filter_complex", ";".join(filters),
            "-map", "[aout]", *_LEAN_AUDIO_FLAGS, str(out_path),
        ]
        await self._run_ffmpeg(cmd)
        return out_path

    # ── низкоуровневые помощники ──

    async def _run_ffmpeg(self, cmd: list[str]) -> None:
        logger.debug("ffmpeg: %s", " ".join(cmd))
        process = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(process.communicate(), timeout=180)
        if process.returncode != 0:
            raise RenderError(stderr.decode("utf-8", errors="ignore")[-2000:])

    @staticmethod
    async def _probe_duration(path: Path) -> float:
        cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)]
        process = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
        if process.returncode != 0:
            raise RenderError(f"ffprobe failed for {path}: {stderr.decode(errors='ignore')}")
        return float(stdout.decode().strip())

    @staticmethod
    def _find_font() -> str | None:
        for candidate in _FONT_CANDIDATES:
            if Path(candidate).exists():
                return candidate
        return None


def _escape_drawtext(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\u2019")
        .replace("%", "\\%")
    )
