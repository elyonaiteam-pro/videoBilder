"""
VideoGenerationPipeline — New Era.

Держит вместе:
  • AssetLibrary   — локальные материалы (new_main_assets), см. assets/library.py
  • ScriptGenerator — Gemini: идея / сценарий / подбор стикеров, см. services/llm.py
  • TTS provider    — ElevenLabs с fallback на Edge, см. tts/factory.py
  • FFmpegRenderer  — финальная сборка видео, см. video_generation/renderer.py

FSM-хендлеры (bot/handlers.py) обращаются к pipeline.library и pipeline.script_gen
напрямую, чтобы собрать GenerationRequest по шагам диалога, и в конце вызывают
pipeline.generate(request).

Важно: озвучка синтезируется ОТДЕЛЬНО ПО КАЖДОЙ СЦЕНЕ (не одним куском на весь
сценарий) — так рендерер узнаёт РЕАЛЬНУЮ длительность речи для каждой сцены
(через ffprobe) и тайминг субтитров/стикеров совпадает с голосом даже если
голос звучит быстрее/медленнее, чем предполагал Gemini в поле duration.
"""
import logging
from datetime import UTC, datetime

from assets.library import AssetLibrary
from config.settings import Settings
from services.history import HistoryRepository
from services.llm import ScriptGenerator
from services.models import GenerationRequest, GenerationResult
from tts.factory import create_tts_provider
from video_generation.renderer import FFmpegRenderer

logger = logging.getLogger(__name__)


class VideoGenerationPipeline:
    def __init__(self, settings: Settings, history: HistoryRepository) -> None:
        self.settings   = settings
        self.history    = history
        self.library    = AssetLibrary(settings)
        self.script_gen = ScriptGenerator(settings, history)
        self.tts        = create_tts_provider(settings)
        self.renderer   = FFmpegRenderer(settings)

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        scene_audio_paths = await self._synthesize_scene_audio(request, stamp)

        video_path = None
        try:
            video_path = await self.renderer.render(request, scene_audio_paths)
        except Exception:
            logger.exception("Рендер видео не удался")

        result = GenerationResult(
            request=request,
            script=request.script,
            video_path=video_path,
            voice_path=scene_audio_paths[0] if scene_audio_paths else None,
        )
        await self.history.save(result)
        return result

    async def _synthesize_scene_audio(self, request: GenerationRequest, stamp: str) -> list:
        paths = []
        for scene in request.script.scenes:
            target = self.settings.cache_dir / "audio" / f"voice_{stamp}_{scene.index}.mp3"
            try:
                synthesized = await self.tts.synthesize(
                    scene.voiceover,
                    target,
                    self.settings.default_tts_voice,
                    self.settings.speech_rate,
                    self.settings.speech_pitch,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("TTS failed for scene %d: %s", scene.index, exc)
                synthesized = await self._silent_fallback(target, seconds=max(1.5, scene.duration))
            paths.append(synthesized)
        return paths

    @staticmethod
    async def _silent_fallback(target, seconds: float):
        import asyncio

        target.parent.mkdir(parents=True, exist_ok=True)
        process = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
            "-t", str(seconds), str(target),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        await process.communicate()
        return target
