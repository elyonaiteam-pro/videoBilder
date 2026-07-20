"""
ScriptGenerator — New Era.

Три источника идеи для ролика (см. plans_for_videobilder.txt):
  1) "template" — готовая идея из templates/library.py, ничего не генерируем,
     просто отдаём angle/hook_pattern пользователю на подтверждение.
  2) "custom"   — пользователь сам пишет идею текстом.
  3) "gemini"   — Gemini придумывает короткую идею сама (generate_idea()).

После того как идея подтверждена (и уже выбраны фон/стикер-пак/музыка/баннер),
вызывается generate_script() — он и пишет полный сценарий (voiceover +
покадровые on-screen тексты) под референсный стиль AtlantaVPN.

Отдельно select_stickers() просит Gemini расставить стикеры из уже выбранного
пользователем пака стикеров по сценам сценария.

Никаких visual_prompt/asset_keywords для внешних стоков — визуал ролика это
один локальный фон (уже выбран пользователем) + стикеры поверх, больше
Gemini ничего для визуала не подбирает.
"""

import json
import logging
from random import SystemRandom
from typing import Any

import aiohttp
from pydantic import ValidationError

from assets.library import StickerPack
from config.settings import Settings
from services.history import HistoryRepository
from services.models import Scene, StickerPlacement, VideoScript
from templates.library import VideoTemplate

logger = logging.getLogger(__name__)

GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

SCRIPT_SYSTEM_PROMPT = """Ты профессиональный сценарист коротких вертикальных видео для TikTok/Reels/YouTube Shorts.
Ты пишешь нативный контент для AtlantaVPN — VPN-сервиса для русскоязычной аудитории.

СТИЛЬ (строго по образцу):
• ХУК — первые 2-3 секунды: один громкий тезис, вопрос или провокация. Зритель должен остановиться.
• Короткие сцены: 2-4 секунды каждая. Без затяжных объяснений.
• On-screen текст: МАКСИМУМ 5-7 слов на экране. Только суть. Крупно.
• Voiceover: разговорный, живой. Как будто друг объясняет. Без "данный продукт".
• Без агрессивной рекламы. Нативно, полезно, с иронией если уместно.
• CTA мягкий: "ссылка в шапке профиля", "в описании", "сохрани чтобы не потерять".
• Видео должно РЕКЛАМИРОВАТЬ AtlantaVPN, но не выглядеть как реклама.

ЗАПРЕЩЕНО: обещать обход законов, гарантировать анонимность, агрессивные продажи.

Отвечай ТОЛЬКО валидным JSON без markdown-блоков и без пояснений."""

IDEA_SYSTEM_PROMPT = """Ты придумываешь короткие идеи для рекламных видео AtlantaVPN (VPN для России и СНГ).
Идея — это 1-2 предложения: конкретный угол/повод для ролика (не сам сценарий, только концепция).
Учитывай актуальный контекст: блокировки сервисов, замедления, штрафы за VPN, новости о рунете —
но не выдумывай конкретных фактов, которых не знаешь, обобщай осторожно.
Идея должна быть свежей, не банальной, и не повторять уже использованные.

Отвечай ТОЛЬКО валидным JSON без markdown-блоков и без пояснений."""

STICKER_SYSTEM_PROMPT = """Ты подбираешь анимированные стикеры (реакции) для рекламного видео AtlantaVPN
из уже заданного набора стикеров пользователя. Стикеры усиливают эмоцию сцены (удивление,
смех, одобрение, спокойствие) — не должны быть случайными.

Отвечай ТОЛЬКО валидным JSON без markdown-блоков и без пояснений."""


class ScriptGenerator:
    def __init__(self, settings: Settings, history: HistoryRepository) -> None:
        self.settings = settings
        self.history = history

    # ---------- идея ----------

    async def generate_idea(self, hint: str | None = None) -> str:
        """Просит Gemini придумать короткую (1-2 предложения) идею ролика."""
        if not self.settings.gemini_api_key:
            return self._fallback_idea()
        used = await self.history.used_scripts(limit=20)
        avoid_titles = [item.title for item in used]
        payload = {
            "task": "Придумай одну свежую идею для рекламного ролика AtlantaVPN (не сценарий, только концепция).",
            "hint": hint or "на твоё усмотрение — что сейчас максимально зайдёт аудитории 18-35 РФ/СНГ",
            "avoid_titles": avoid_titles,
            "required_json_schema": {"idea": "str — 1-2 предложения, конкретный угол для ролика"},
        }
        try:
            content = await self._call_gemini(IDEA_SYSTEM_PROMPT, json.dumps(payload, ensure_ascii=False))
            data = self._parse_json_response(content)
            idea = str(data["idea"]).strip()
            if idea:
                return idea
        except (aiohttp.ClientError, json.JSONDecodeError, KeyError, ValueError) as exc:
            logger.warning("Gemini idea generation failed, using fallback: %s", exc)
        return self._fallback_idea()

    @staticmethod
    def _fallback_idea() -> str:
        return SystemRandom().choice(
            [
                "Показать, как сервис перестаёт открываться прямо во время важного звонка — и один клик всё чинит.",
                "Сравнить скорость с VPN и без на реальном примере — неожиданный результат.",
                "История про друга, которого спалили по IP в игре — и как этого избежать.",
            ]
        )

    # ---------- финальный сценарий ----------

    async def generate_script(
        self,
        idea_text: str,
        template: VideoTemplate | None = None,
    ) -> VideoScript:
        used = await self.history.used_scripts(limit=20)
        if not self.settings.gemini_api_key:
            return self._fallback_script(idea_text, template)
        prompt = self._build_script_prompt(idea_text, template, used)
        try:
            content = await self._call_gemini(SCRIPT_SYSTEM_PROMPT, prompt)
            data = self._parse_json_response(content)
            data["template_id"] = template.id if template else "custom"
            script = VideoScript.model_validate(data)
            logger.info("Generated script: %s (%d scenes)", script.title, len(script.scenes))
            return script
        except (aiohttp.ClientError, json.JSONDecodeError, ValidationError, KeyError, ValueError) as exc:
            logger.warning("Gemini script generation failed, using fallback: %s", exc)
            return self._fallback_script(idea_text, template)

    def _build_script_prompt(
        self,
        idea_text: str,
        template: VideoTemplate | None,
        used: list[VideoScript],
    ) -> str:
        used_titles = [item.title for item in used]
        payload: dict[str, Any] = {
            "task": (
                "Напиши сценарий вертикального видео 9:16, длина 15-25 секунд, для AtlantaVPN. "
                "Аудитория: Россия и СНГ, 18-35 лет. Платформа: TikTok / Instagram Reels / YouTube Shorts."
            ),
            "idea": idea_text,
            "structure": {
                "scene_1_hook": "2-3 сек — один тезис, вопрос или факт. Зритель должен остановиться.",
                "scene_2_pain": "3-4 сек — проблема близко и знакома. Без воды.",
                "scene_3_solution": "4-5 сек — AtlantaVPN решает. Показываем как.",
                "scene_4_result": "3-4 сек — конкретный результат/цифра/ощущение.",
                "scene_5_cta": "2-3 сек — мягкий призыв. Ссылка в профиле.",
            },
            "on_screen_text_rules": [
                "МАКСИМУМ 6 слов на сцену",
                "Только caps или Title Case для заголовков",
                "Без длинных предложений — только суть",
                "Хук должен быть провокационным или вопросом",
            ],
            "avoid_titles": used_titles,
            "required_json_schema": {
                "title": "str — заголовок видео (для внутреннего использования)",
                "hook": "str — первая фраза которую скажет голос",
                "script": "str — краткое описание структуры сцен через ' → '",
                "voiceover": "str — финальный полный текст для синтеза речи, разговорный стиль",
                "on_screen_texts": ["str — тексты на экране по сценам, по одному на сцену"],
                "publication_description": "str — описание для публикации с эмодзи",
                "hashtags": ["str"],
                "scenes": [
                    {
                        "index": "int 1-5",
                        "title": "str — внутреннее название сцены (хук/боль/решение/результат/cta)",
                        "duration": "float 2.0-5.0 секунд",
                        "voiceover": "str — что говорит голос в этой сцене",
                        "on_screen_text": "str — МАКСИМУМ 6 слов крупно на экране",
                    }
                ],
            },
            "constraints": [
                "Строго 4-5 сцен",
                "Общая длина 15-25 секунд",
                "on_screen_text не более 6 слов",
                "Хук в первые 3 секунды",
                "Мягкий CTA без давления",
                "Не повторять темы из avoid_titles",
            ],
        }
        if template is not None:
            payload["template"] = {
                "id": template.id,
                "name": template.name,
                "angle": template.angle,
                "hook_pattern": template.hook_pattern,
            }
        return json.dumps(payload, ensure_ascii=False)

    def _fallback_script(self, idea_text: str, template: VideoTemplate | None) -> VideoScript:
        subject = idea_text.strip() or "приватность в сети"
        scenes = [
            Scene(
                index=1, title="Хук", duration=2.5,
                voiceover=f"Ты точно не делаешь это, когда речь про {subject}?",
                on_screen_text="ТЫ ДЕЛАЕШЬ ЭТО?",
            ),
            Scene(
                index=2, title="Боль", duration=3.5,
                voiceover="Без защиты твои данные видны всем вокруг.",
                on_screen_text="ТВОИ ДАННЫЕ ОТКРЫТЫ",
            ),
            Scene(
                index=3, title="Решение", duration=4.5,
                voiceover="AtlantaVPN шифрует соединение за секунду. Один клик — и ты в безопасности.",
                on_screen_text="ОДИН КЛИК — ЗАЩИТА",
            ),
            Scene(
                index=4, title="Результат", duration=3.5,
                voiceover="Всё работает как обычно — просто безопасно.",
                on_screen_text="РАБОТАЕТ. БЕЗОПАСНО.",
            ),
            Scene(
                index=5, title="CTA", duration=2.5,
                voiceover="Ссылка в шапке профиля. Не откладывай.",
                on_screen_text="ССЫЛКА В ПРОФИЛЕ",
            ),
        ]
        voiceover = " ".join(scene.voiceover for scene in scenes)
        return VideoScript(
            title=f"AtlantaVPN: {subject[:60]}",
            template_id=template.id if template else "custom",
            hook=scenes[0].voiceover,
            script=" → ".join(scene.title for scene in scenes),
            voiceover=voiceover,
            on_screen_texts=[scene.on_screen_text for scene in scenes],
            publication_description=f"🔒 {subject}. AtlantaVPN — попробуй бесплатно.",
            hashtags=["#vpn", "#atlantavpn", "#безопасность", "#лайфхак", "#shorts"],
            scenes=scenes,
        )

    # ---------- подбор стикеров ----------

    async def select_stickers(
        self,
        script: VideoScript,
        pack: StickerPack,
        count: int = 8,
    ) -> list[StickerPlacement]:
        """
        Просит Gemini расставить count стикеров (индексы 0..len(pack.stickers)-1)
        по сценам сценария. Если Gemini недоступна — расставляем равномерно
        случайными стикерами из пака (не оставляем ролик совсем без стикеров).
        """
        count = min(count, len(pack.stickers))
        if not self.settings.gemini_api_key or count == 0:
            return self._fallback_sticker_placements(script, pack, count)

        payload = {
            "task": f"Выбери и расставь {count} стикеров по сценам ролика.",
            "sticker_pack": pack.name,
            "available_sticker_indices": list(range(len(pack.stickers))),
            "scenes": [
                {"index": s.index, "title": s.title, "voiceover": s.voiceover, "on_screen_text": s.on_screen_text}
                for s in script.scenes
            ],
            "required_json_schema": {
                "stickers": [{"sticker_index": "int из available_sticker_indices", "scene_index": "int — Scene.index"}]
            },
            "constraints": [
                f"Ровно {count} элементов в stickers",
                "sticker_index только из available_sticker_indices, без повторов",
                "Распредели по разным сценам, не толпи всё в одной",
            ],
        }
        try:
            content = await self._call_gemini(STICKER_SYSTEM_PROMPT, json.dumps(payload, ensure_ascii=False))
            data = self._parse_json_response(content)
            placements = [StickerPlacement.model_validate(item) for item in data["stickers"]]
            valid_indices = set(range(len(pack.stickers)))
            valid_scenes = {s.index for s in script.scenes}
            placements = [
                p for p in placements if p.sticker_index in valid_indices and p.scene_index in valid_scenes
            ]
            if placements:
                return placements[:count]
        except (aiohttp.ClientError, json.JSONDecodeError, ValidationError, KeyError, ValueError) as exc:
            logger.warning("Gemini sticker selection failed, using fallback: %s", exc)
        return self._fallback_sticker_placements(script, pack, count)

    @staticmethod
    def _fallback_sticker_placements(script: VideoScript, pack: StickerPack, count: int) -> list[StickerPlacement]:
        rng = SystemRandom()
        indices = list(range(len(pack.stickers)))
        rng.shuffle(indices)
        scene_indices = [s.index for s in script.scenes] or [1]
        placements = []
        for i in range(min(count, len(indices))):
            placements.append(
                StickerPlacement(sticker_index=indices[i], scene_index=scene_indices[i % len(scene_indices)])
            )
        return placements

    # ---------- низкоуровневый вызов Gemini ----------

    async def _call_gemini(self, system_prompt: str, user_payload: str) -> str:
        url = GEMINI_API_URL.format(model=self.settings.gemini_model)
        payload: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_payload}]}],
            "generationConfig": {
                "temperature": self.settings.llm_temperature,
                "responseMimeType": "application/json",
            },
        }
        params = {"key": self.settings.gemini_api_key}
        timeout = aiohttp.ClientTimeout(total=90)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, params=params, json=payload) as response:
                response_text = await response.text()
                if response.status >= 400:
                    raise ValueError(f"Gemini API error {response.status}: {response_text[:500]}")
                data = json.loads(response_text)
        candidates = data.get("candidates") or []
        if not candidates:
            raise ValueError("Gemini response has no candidates")
        parts = candidates[0].get("content", {}).get("parts") or []
        text = "".join(part.get("text", "") for part in parts)
        if not text.strip():
            raise ValueError("Gemini response text is empty")
        return text

    @staticmethod
    def _parse_json_response(content: str) -> dict[str, Any]:
        stripped = content.strip()
        if stripped.startswith("```"):
            stripped = stripped.removeprefix("```json").removeprefix("```").strip()
            stripped = stripped.removesuffix("```").strip()
        return json.loads(stripped)
