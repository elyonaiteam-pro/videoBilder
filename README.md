# AtlantaVPN Video Bot (videobilder) — New Era

Личный Telegram-бот, который по шагам (FSM-диалог) собирает вертикальные
рекламные ролики для AtlantaVPN: сценарий и подбор стикеров пишет Gemini,
озвучка — ElevenLabs (голоса Dmitriy D. / Adam) с fallback на бесплатный
Edge TTS, все визуальные материалы (фоны, стикеры, музыка, баннеры) — только
локальные, из `new_main_assets/`, без внешних стоков.

Полный план и обоснование архитектурных решений — `plans_for_videobilder.txt`.

## Статус рефакторинга ("New Era")

- ✅ Фаза 1 — конфиг, локальные ассеты (`assets/library.py`), очистка
- ✅ Фаза 2 — ElevenLabs TTS (fallback → Edge TTS), Gemini 3
- ✅ Фаза 3 — FSM-диалог (`bot/handlers.py`, `bot/states.py`), Gemini-сценарист
  и подбор стикеров (`services/llm.py`)
- ✅ Фаза 4 — ffmpeg-сборка готового видео (`video_generation/renderer.py`)
- ✅ Инфраструктура — деплой на Google Cloud Run + доставка видео через
  Google Drive (`bot/delivery.py`). См. `README_CLOUDRUN_DEPLOY.md`.
  (`README_HF_DEPLOY.md`/`infra/vercel-proxy/` — от изначального плана деплоя
  на Hugging Face Spaces, оставлены на случай, если понадобится вернуться —
  HF в моменте закрыли бесплатный Docker SDK.)

## Диалог бота

`/start` → источник идеи (шаблон / своя / от Gemini) → подтверждение идеи →
тема фона (тёмный/светлый) → номер фона по превью → пак стикеров → музыка →
баннер-концовка → финальное подтверждение → генерация.

## Архитектура

```text
/bot                 FSM-хендлеры, main.py (webhook на Cloud Run / polling локально), delivery.py (Google Drive)
/config              Settings (pydantic-settings)
/services            models.py (Pydantic-схемы), llm.py (Gemini), history.py (SQLite)
/video_generation    pipeline.py (TTS по сценам + сборка запроса), renderer.py (ffmpeg-сборка)
/tts                 elevenlabs.py (основной) → edge.py (fallback) → silent.py
/assets              library.py (реестр new_main_assets/), provider.py (Old Era, больше не используется)
/templates           20 шаблонных идей
/new_main_assets     ВСЕ материалы для роликов: фоны, стикеры, музыка, баннеры
/infra/vercel-proxy  прокси Telegram API (не нужен на Cloud Run, оставлен про запас)
```

## Локальный запуск (Windows, polling-режим)

```powershell
cd E:\Projects\videobilder-main
pip install -e .
# заполнить .env (см. .env.example) — TELEGRAM_BOT_TOKEN обязателен,
# PUBLIC_BASE_URL оставить пустым, чтобы бот запустился в режиме polling
python -m bot.main
```

## Деплой на Google Cloud Run

См. `README_CLOUDRUN_DEPLOY.md` — пошагово: включение API, первый деплой,
переменные окружения/секреты, регистрация вебхука, команды для ручного
включения/выключения бота.
