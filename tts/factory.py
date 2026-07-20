from config.settings import Settings
from tts.base import TTSProvider
from tts.edge import EdgeTTSProvider
from tts.elevenlabs import ElevenLabsTTSProvider
from tts.silent import SilentTTSProvider


def create_tts_provider(settings: Settings) -> TTSProvider:
    """
    New Era: движок по умолчанию — ElevenLabs (голоса Dmitriy D. / Adam), но он сам
    внутри себя откатывается на Edge TTS (ru-RU-DmitryNeural), если ключа/кредитов
    нет или запрос падает — см. tts/elevenlabs.py. Поэтому даже DEFAULT_TTS_ENGINE=
    elevenlabs без настроенного аккаунта работает "из коробки" на бесплатном Edge.
    """
    engine = settings.default_tts_engine
    if engine == "elevenlabs":
        return ElevenLabsTTSProvider(
            api_key=settings.elevenlabs_api_key,
            model_id=settings.elevenlabs_model,
            voice_id_primary=settings.elevenlabs_voice_id_dmitriy,
            voice_id_secondary=settings.elevenlabs_voice_id_adam,
            fallback=EdgeTTSProvider(),
        )
    if engine == "edge":
        return EdgeTTSProvider()
    if engine == "silent":
        return SilentTTSProvider()
    raise ValueError(f"Unsupported TTS engine: {engine}")
