"""
ElevenLabs TTS (голоса Dmitriy D. / Adam) с автоматическим fallback.

У пользователя на балансе ElevenLabs может быть 0 кредитов (бесплатный тариф
даёт ограниченную квоту символов в месяц) — в таком случае API вернёт 401/402,
и мы должны прозрачно, без падения генерации, откатиться на бесплатный
Edge TTS (голос ru-RU-DmitryNeural — тоже "Дмитрий", просто от Microsoft).

Порядок попыток при синтезе:
  1) ElevenLabs, voice_id = ELEVENLABS_VOICE_ID_DMITRIY (если задан)
  2) ElevenLabs, voice_id = ELEVENLABS_VOICE_ID_ADAM (если первый не сработал
     и задан свой запасной id)
  3) Edge TTS (ru-RU-DmitryNeural), который сам уже умеет падать в gTTS —
     см. tts/edge.py. То есть в самом крайнем случае озвучка всё равно будет.
"""
import logging
from pathlib import Path

import aiohttp

from tts.base import TTSProvider
from tts.edge import EdgeTTSProvider

logger = logging.getLogger(__name__)

_API_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"


class ElevenLabsTTSProvider:
    def __init__(
        self,
        api_key: str,
        model_id: str,
        voice_id_primary: str,
        voice_id_secondary: str = "",
        fallback: TTSProvider | None = None,
    ) -> None:
        self.api_key = api_key
        self.model_id = model_id
        self.voice_ids = [v for v in (voice_id_primary, voice_id_secondary) if v]
        self.fallback = fallback or EdgeTTSProvider()

    async def synthesize(self, text: str, target_path: Path, voice: str, rate: str, pitch: str) -> Path:
        target_path.parent.mkdir(parents=True, exist_ok=True)

        if not self.api_key or not self.voice_ids:
            logger.info("ElevenLabs не настроен (нет api_key/voice_id), сразу используем Edge TTS")
            return await self.fallback.synthesize(text, target_path, voice, rate, pitch)

        for voice_id in self.voice_ids:
            ok = await self._try_elevenlabs(text, target_path, voice_id)
            if ok:
                return target_path

        logger.warning("ElevenLabs недоступен (кредиты/лимит/ошибка) — переключаемся на Edge TTS")
        return await self.fallback.synthesize(text, target_path, voice, rate, pitch)

    async def _try_elevenlabs(self, text: str, target_path: Path, voice_id: str) -> bool:
        url = _API_URL.format(voice_id=voice_id)
        headers = {
            "xi-api-key": self.api_key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        }
        payload = {
            "text": text,
            "model_id": self.model_id,
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
        }
        try:
            timeout = aiohttp.ClientTimeout(total=30)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, headers=headers, json=payload) as resp:
                    if resp.status == 200:
                        data = await resp.read()
                        if len(data) > 100:
                            target_path.write_bytes(data)
                            logger.info(
                                "ElevenLabs TTS success: %s (voice_id=%s)", target_path.name, voice_id
                            )
                            return True
                        logger.warning("ElevenLabs вернул пустой аудио-файл (voice_id=%s)", voice_id)
                        return False
                    body = await resp.text()
                    # 401 — неверный ключ, 402 — нет кредитов, 429 — лимит запросов
                    logger.warning(
                        "ElevenLabs HTTP %s (voice_id=%s): %s",
                        resp.status, voice_id, body[:200],
                    )
                    return False
        except Exception as exc:  # noqa: BLE001
            logger.warning("ElevenLabs запрос упал (voice_id=%s): %s", voice_id, exc)
            return False
