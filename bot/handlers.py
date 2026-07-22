"""
bot/handlers.py — New Era FSM-диалог.

Полный флоу (см. plans_for_videobilder.txt):
  /start
    → источник идеи: шаблон / своя / от Gemini
    → (для шаблона: список шаблонов; для своей: ввод текста; для Gemini: генерация)
    → подтверждение идеи
    → тема фона: тёмный / светлый
    → номер фона (превью присылаются альбомом, пользователь пишет цифру)
    → пак стикеров
    → фоновая музыка
    → баннер-концовка
    → финальное подтверждение → генерация

После каждого шага бот удаляет своё предыдущее сообщение с выбором.

Готовое видео (Фаза 4, video_generation/renderer.py) отправляется в Telegram
напрямую (Render не блокирует исходящие запросы к Telegram, в отличие от HF —
Google Drive как промежуточное звено больше не нужен, см. bot/delivery.py,
он остаётся в репо неиспользуемым на случай переезда на платформу с фаерволом).
"""
import logging
from pathlib import Path

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
)

from bot.states import GenerationFlow
from services.history import HistoryRepository
from services.models import GenerationRequest
from templates.library import TEMPLATES
from video_generation.pipeline import VideoGenerationPipeline

logger = logging.getLogger(__name__)
router = Router()

HELP_TEXT = (
    "🎬 <b>AtlantaVPN Video Bot</b>\n\n"
    "/start — собрать новый ролик по шагам (идея → фон → стикеры → музыка → баннер)\n"
    "/history — последние генерации"
)


async def _cleanup(bot, chat_id: int, state: FSMContext) -> None:
    data = await state.get_data()
    ids: list[int] = []
    if data.get("menu_message_id"):
        ids.append(data["menu_message_id"])
    ids.extend(data.get("extra_message_ids") or [])
    for mid in ids:
        try:
            await bot.delete_message(chat_id, mid)
        except Exception:  # noqa: BLE001
            pass
    await state.update_data(menu_message_id=None, extra_message_ids=[])


async def _reset(message: Message, state: FSMContext) -> None:
    await state.clear()


@router.message(Command("start"))
@router.message(Command("help"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    await _reset(message, state)
    await _show_idea_source_menu(message, state)


@router.message(Command("history"))
async def history_cmd(message: Message, history_repo: HistoryRepository) -> None:
    rows = await history_repo.latest(limit=10)
    if not rows:
        await message.answer("История пустая — начни новый ролик командой /start")
        return
    text = "<b>Последние генерации:</b>\n" + "\n".join(
        f"• {row['created_at'][:16]}: {row['title']}" for row in rows
    )
    await message.answer(text, parse_mode="HTML")


async def _show_idea_source_menu(message: Message, state: FSMContext) -> None:
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Шаблонная идея", callback_data="idea_src:template")],
        [InlineKeyboardButton(text="✍️ Своя идея", callback_data="idea_src:custom")],
        [InlineKeyboardButton(text="🤖 Идея от Gemini", callback_data="idea_src:gemini")],
    ])
    sent = await message.answer(
        "🎬 <b>Новый ролик AtlantaVPN</b>\n\nОткуда взять идею для ролика?",
        parse_mode="HTML",
        reply_markup=kb,
    )
    await state.set_state(GenerationFlow.choosing_idea_source)
    await state.update_data(menu_message_id=sent.message_id)


@router.callback_query(GenerationFlow.choosing_idea_source, F.data.startswith("idea_src:"))
async def on_idea_source(callback: CallbackQuery, state: FSMContext, pipeline: VideoGenerationPipeline) -> None:
    await callback.answer()
    source = callback.data.split(":", 1)[1]
    await state.update_data(idea_source=source)
    await _cleanup(callback.message.bot, callback.message.chat.id, state)

    if source == "template":
        await _show_template_list(callback.message, state)
    elif source == "custom":
        sent = await callback.message.answer("✍️ Напиши идею ролика одним сообщением:")
        await state.update_data(menu_message_id=sent.message_id)
        await state.set_state(GenerationFlow.entering_custom_idea)
    elif source == "gemini":
        status = await callback.message.answer("🤖 Gemini придумывает идею…")
        idea = await pipeline.script_gen.generate_idea()
        await status.delete()
        await state.update_data(idea_text=idea, template_index=None)
        await _show_idea_confirmation(callback.message, state, idea)


async def _show_template_list(message: Message, state: FSMContext) -> None:
    rows = [
        [InlineKeyboardButton(text=f"{i + 1}. {t.name}", callback_data=f"tpl:{i}")]
        for i, t in enumerate(TEMPLATES)
    ]
    sent = await message.answer(
        "📋 Выбери шаблонную идею:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await state.update_data(menu_message_id=sent.message_id)
    await state.set_state(GenerationFlow.choosing_template)


@router.callback_query(GenerationFlow.choosing_template, F.data.startswith("tpl:"))
async def on_template_chosen(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    idx = int(callback.data.split(":", 1)[1])
    template = TEMPLATES[idx]
    await state.update_data(idea_text=template.angle, template_index=idx)
    await _cleanup(callback.message.bot, callback.message.chat.id, state)
    await _show_idea_confirmation(callback.message, state, template.angle)


@router.message(GenerationFlow.entering_custom_idea, F.text)
async def on_custom_idea(message: Message, state: FSMContext) -> None:
    idea = message.text.strip()
    await state.update_data(idea_text=idea, template_index=None)
    await _cleanup(message.bot, message.chat.id, state)
    await _show_idea_confirmation(message, state, idea)


async def _show_idea_confirmation(message: Message, state: FSMContext, idea: str) -> None:
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Подтвердить", callback_data="idea_confirm:yes"),
        InlineKeyboardButton(text="🔄 Другой вариант", callback_data="idea_confirm:retry"),
    ]])
    sent = await message.answer(f"💡 <b>Идея:</b>\n{idea}", parse_mode="HTML", reply_markup=kb)
    await state.update_data(menu_message_id=sent.message_id)
    await state.set_state(GenerationFlow.confirming_idea)


@router.callback_query(GenerationFlow.confirming_idea, F.data.startswith("idea_confirm:"))
async def on_idea_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    action = callback.data.split(":", 1)[1]
    await _cleanup(callback.message.bot, callback.message.chat.id, state)
    if action == "retry":
        await _show_idea_source_menu(callback.message, state)
        return
    await _show_theme_menu(callback.message, state)


async def _show_theme_menu(message: Message, state: FSMContext) -> None:
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🌑 Тёмный фон", callback_data="theme:dark"),
        InlineKeyboardButton(text="☀️ Светлый фон", callback_data="theme:light"),
    ]])
    sent = await message.answer("🎨 Какой фон для ролика?", reply_markup=kb)
    await state.update_data(menu_message_id=sent.message_id)
    await state.set_state(GenerationFlow.choosing_theme)


@router.callback_query(GenerationFlow.choosing_theme, F.data.startswith("theme:"))
async def on_theme_chosen(callback: CallbackQuery, state: FSMContext, pipeline: VideoGenerationPipeline) -> None:
    await callback.answer()
    theme = callback.data.split(":", 1)[1]
    await state.update_data(background_theme=theme)
    await _cleanup(callback.message.bot, callback.message.chat.id, state)

    options = pipeline.library.list_backgrounds(theme)  # type: ignore[arg-type]
    if not options:
        await callback.message.answer("⚠️ Нет доступных фонов для этой темы, проверь new_main_assets.")
        return

    media = [InputMediaPhoto(media=FSInputFile(o.preview_path), caption=str(o.number)) for o in options[:10]]
    album = await callback.message.answer_media_group(media)
    prompt = await callback.message.answer("Напиши цифру подходящего фона:")
    await state.update_data(
        extra_message_ids=[m.message_id for m in album],
        menu_message_id=prompt.message_id,
        available_backgrounds=[o.number for o in options],
    )
    await state.set_state(GenerationFlow.choosing_background)


@router.message(GenerationFlow.choosing_background, F.text)
async def on_background_number(message: Message, state: FSMContext, pipeline: VideoGenerationPipeline) -> None:
    data = await state.get_data()
    available = data.get("available_backgrounds") or []
    text = (message.text or "").strip()
    if not text.isdigit() or int(text) not in available:
        await message.answer(f"Введи одну из цифр: {', '.join(map(str, available))}")
        return
    number = int(text)
    theme = data["background_theme"]
    video_path = pipeline.library.get_background_video(theme, number)  # type: ignore[arg-type]
    if video_path is None:
        await message.answer("⚠️ Не нашёл видео для этого фона, попробуй другую цифру.")
        return

    await state.update_data(background_number=number, background_path=str(video_path))
    await _cleanup(message.bot, message.chat.id, state)
    await _show_sticker_pack_menu(message, state, pipeline)


async def _show_sticker_pack_menu(message: Message, state: FSMContext, pipeline: VideoGenerationPipeline) -> None:
    packs = pipeline.library.list_sticker_packs()
    rows = [
        [InlineKeyboardButton(text=f"{p.name} ({len(p.stickers)})", callback_data=f"pack:{i}")]
        for i, p in enumerate(packs)
    ]
    sent = await message.answer("🦆 Какой пак стикеров использовать?", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await state.update_data(menu_message_id=sent.message_id, available_packs=[p.name for p in packs])
    await state.set_state(GenerationFlow.choosing_sticker_pack)


@router.callback_query(GenerationFlow.choosing_sticker_pack, F.data.startswith("pack:"))
async def on_pack_chosen(callback: CallbackQuery, state: FSMContext, pipeline: VideoGenerationPipeline) -> None:
    await callback.answer()
    idx = int(callback.data.split(":", 1)[1])
    data = await state.get_data()
    pack_name = data["available_packs"][idx]
    await state.update_data(sticker_pack_name=pack_name)
    await _cleanup(callback.message.bot, callback.message.chat.id, state)
    await _show_song_menu(callback.message, state, pipeline)


async def _show_song_menu(message: Message, state: FSMContext, pipeline: VideoGenerationPipeline) -> None:
    songs = pipeline.library.list_songs()
    rows = [
        [InlineKeyboardButton(text=f"🎵 {s.stem[:28]}", callback_data=f"song:{i}")]
        for i, s in enumerate(songs)
    ]
    sent = await message.answer("🎵 Фоновая музыка:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await state.update_data(menu_message_id=sent.message_id, available_songs=[str(s) for s in songs])
    await state.set_state(GenerationFlow.choosing_song)


@router.callback_query(GenerationFlow.choosing_song, F.data.startswith("song:"))
async def on_song_chosen(callback: CallbackQuery, state: FSMContext, pipeline: VideoGenerationPipeline) -> None:
    await callback.answer()
    idx = int(callback.data.split(":", 1)[1])
    data = await state.get_data()
    song_path = data["available_songs"][idx]
    await state.update_data(song_path=song_path)
    await _cleanup(callback.message.bot, callback.message.chat.id, state)
    await _show_banner_menu(callback.message, state, pipeline)


async def _show_banner_menu(message: Message, state: FSMContext, pipeline: VideoGenerationPipeline) -> None:
    banners = pipeline.library.list_banners()
    rows = [
        [InlineKeyboardButton(text=f"🏁 {b.stem}", callback_data=f"banner:{i}")]
        for i, b in enumerate(banners)
    ]
    sent = await message.answer("🏁 Баннер-концовка:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await state.update_data(menu_message_id=sent.message_id, available_banners=[str(b) for b in banners])
    await state.set_state(GenerationFlow.choosing_banner)


@router.callback_query(GenerationFlow.choosing_banner, F.data.startswith("banner:"))
async def on_banner_chosen(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    idx = int(callback.data.split(":", 1)[1])
    data = await state.get_data()
    banner_path = data["available_banners"][idx]
    await state.update_data(banner_path=banner_path)
    await _cleanup(callback.message.bot, callback.message.chat.id, state)
    await _show_final_confirmation(callback.message, state)


async def _show_final_confirmation(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    summary = (
        "🎬 <b>Проверь перед генерацией:</b>\n\n"
        f"💡 Идея: {data['idea_text']}\n"
        f"🎨 Фон: {data['background_theme']} #{data['background_number']}\n"
        f"🦆 Стикеры: {data['sticker_pack_name']}\n"
        f"🎵 Музыка: {Path(data['song_path']).stem}\n"
        f"🏁 Баннер: {Path(data['banner_path']).stem}"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🎬 Сгенерировать", callback_data="confirm_gen:yes"),
        InlineKeyboardButton(text="❌ Начать заново", callback_data="confirm_gen:cancel"),
    ]])
    sent = await message.answer(summary, parse_mode="HTML", reply_markup=kb)
    await state.update_data(menu_message_id=sent.message_id)
    await state.set_state(GenerationFlow.confirming_generation)


@router.callback_query(GenerationFlow.confirming_generation, F.data.startswith("confirm_gen:"))
async def on_confirm_generation(
    callback: CallbackQuery,
    state: FSMContext,
    pipeline: VideoGenerationPipeline,
) -> None:
    await callback.answer()
    action = callback.data.split(":", 1)[1]
    await _cleanup(callback.message.bot, callback.message.chat.id, state)

    if action == "cancel":
        await state.clear()
        await _show_idea_source_menu(callback.message, state)
        return

    data = await state.get_data()
    status = await callback.message.answer("⏳ Пишу сценарий и подбираю стикеры…")
    try:
        template = TEMPLATES[data["template_index"]] if data.get("template_index") is not None else None
        script = await pipeline.script_gen.generate_script(data["idea_text"], template)

        pack = pipeline.library.get_sticker_pack(data["sticker_pack_name"])
        placements = await pipeline.script_gen.select_stickers(script, pack, count=8) if pack else []

        request = GenerationRequest(
            idea_source=data["idea_source"],
            idea_text=data["idea_text"],
            background_theme=data["background_theme"],
            background_number=data["background_number"],
            background_path=Path(data["background_path"]),
            sticker_pack_name=data["sticker_pack_name"],
            sticker_placements=placements,
            song_path=Path(data["song_path"]),
            banner_path=Path(data["banner_path"]),
            script=script,
        )

        await status.edit_text("🎬 Собираю видео (озвучка + ffmpeg)…")
        result = await pipeline.generate(request)

        caption = (
            f"✅ <b>{script.title}</b>\n\n"
            f"{script.publication_description}\n{' '.join(script.hashtags)}"
        )

        if result.video_path and result.video_path.exists():
            await status.edit_text("📤 Отправляю видео…")
            await callback.message.answer_video(
                video=FSInputFile(result.video_path), caption=caption, parse_mode="HTML"
            )
            await status.delete()
        else:
            await status.delete()
            text = (
                "⚠️ Рендер видео не удался, но сценарий готов:\n\n"
                f"<b>Voiceover:</b>\n{script.voiceover}\n\n"
                "<b>On-screen:</b>\n" + "\n".join(f"• {t}" for t in script.on_screen_texts)
            )
            await callback.message.answer(text, parse_mode="HTML")
            if result.voice_path and result.voice_path.exists():
                await callback.message.answer_voice(FSInputFile(result.voice_path))
    except Exception as exc:  # noqa: BLE001
        logger.exception("Generation failed")
        await status.edit_text(f"❌ Ошибка генерации.\n<code>{exc}</code>", parse_mode="HTML")
    finally:
        await state.clear()


@router.message(F.text & ~F.text.startswith("/"))
async def free_text_fallback(message: Message) -> None:
    await message.answer("Начни с команды /start, чтобы собрать новый ролик по шагам.")
