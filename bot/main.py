"""
bot/main.py — вебхук на Render (или polling локально).

Режим определяется автоматически:
  • PUBLIC_BASE_URL задан → webhook-режим (aiohttp web-сервер на PORT,
    Render сам назначает порт через переменную окружения, обычно 10000).
    Render НЕ блокирует исходящие запросы к api.telegram.org, поэтому
    TG_PROXY_URL обычно не нужен (оставлен на случай другой платформы).
  • PUBLIC_BASE_URL пуст → обычный polling (локальный запуск на Windows).
"""
import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

from bot.handlers import router
from config.settings import Settings, get_settings
from services.history import HistoryRepository
from video_generation.pipeline import VideoGenerationPipeline

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _build_bot(settings: Settings) -> Bot:
    session = None
    if settings.tg_proxy_url:
        api_server = TelegramAPIServer.from_base(settings.tg_proxy_url, wrap_local_file=False)
        session = AiohttpSession(api=api_server)
        logger.info("Telegram API через прокси: %s", settings.tg_proxy_url)
    return Bot(token=settings.telegram_bot_token, session=session)


async def _health(_request: web.Request) -> web.Response:
    return web.Response(text="ok")


def _build_dispatcher(settings: Settings) -> tuple[Dispatcher, HistoryRepository]:
    dp = Dispatcher()
    dp.include_router(router)

    history = HistoryRepository(settings.sqlite_path)
    pipeline = VideoGenerationPipeline(settings, history)

    dp["pipeline"] = pipeline
    dp["history_repo"] = history
    return dp, history


def main() -> None:
    settings = get_settings()
    bot = _build_bot(settings)
    dp, history = _build_dispatcher(settings)

    if settings.public_base_url:
        _run_webhook(bot, dp, history, settings)
    else:
        asyncio.run(_run_polling(bot, dp, history))


def _run_webhook(bot: Bot, dp: Dispatcher, history: HistoryRepository, settings: Settings) -> None:
    if not settings.webhook_secret:
        raise RuntimeError("WEBHOOK_SECRET обязателен в webhook-режиме (PUBLIC_BASE_URL задан)")

    webhook_path = f"/webhook/{settings.webhook_secret}"
    webhook_url = f"{settings.public_base_url.rstrip('/')}{webhook_path}"

    app = web.Application()
    app.router.add_get("/health", _health)
    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=webhook_path)
    setup_application(app, dp, bot=bot)

    async def _on_startup(_app: web.Application) -> None:
        await history.init()
        await bot.set_webhook(webhook_url, drop_pending_updates=True)
        logger.info("Webhook зарегистрирован: %s", webhook_url)

    app.on_startup.append(_on_startup)
    logger.info("Запуск в режиме webhook на 0.0.0.0:%d", settings.port)
    web.run_app(app, host="0.0.0.0", port=settings.port)


async def _run_polling(bot: Bot, dp: Dispatcher, history: HistoryRepository) -> None:
    await history.init()
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Запуск в режиме polling")
    await dp.start_polling(bot)


if __name__ == "__main__":
    main()
