# Деплой videobilder на Render (Web Service, Docker)

Отдельный Render-аккаунт (не тот, где ресурспак-бот) — свой независимый
бесплатный лимит, никакого биллинга не требуется. Гугл Drive используем как и
раньше (сервисный аккаунт), но JSON-ключ теперь передаём явно через переменную
окружения — на Render нет привязки identity сервиса как на Cloud Run.

## 1. Создать Web Service

1. [dashboard.render.com](https://dashboard.render.com) → **New → Web Service**
2. Connect a repository → авторизуй GitHub (аккаунт `elyonaiteam-pro`) →
   выбери `videoBilder`
3. Настройки:
   - **Name**: `videobilder` (или как хочешь)
   - **Region**: любой, ближе — Frankfurt
   - **Branch**: `main`
   - **Runtime**: **Docker** (Render сам найдёт `Dockerfile` в корне)
   - **Instance Type**: **Free**

## 2. Переменные окружения

**Environment → Add Environment Variable**, добавить все:

| Переменная | Значение |
|---|---|
| `TELEGRAM_BOT_TOKEN` | новый токен (после ротации через @BotFather) |
| `ALLOWED_USER_IDS` | твой Telegram user id |
| `GEMINI_API_KEY` | новый ключ Gemini (после ротации) |
| `GEMINI_MODEL` | `gemini-3-pro-preview` |
| `ELEVENLABS_API_KEY` | новый ключ ElevenLabs (после ротации) |
| `ELEVENLABS_VOICE_ID_DMITRIY` | voice_id Dmitriy D. |
| `ELEVENLABS_VOICE_ID_ADAM` | voice_id Adam |
| `GOOGLE_DRIVE_FOLDER_ID` | ID папки на Google Drive |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | содержимое JSON-ключа сервисного аккаунта целиком (тот файл, что уже скачан) |
| `WEBHOOK_SECRET` | случайная строка (`python -c "import secrets;print(secrets.token_urlsafe(24))"`) |
| `PUBLIC_BASE_URL` | пока не заполняй — впишем после первого деплоя (шаг 4) |

`TG_PROXY_URL` не задаём — Render не блокирует Telegram напрямую.
`PORT` не задаём — Render сам передаёт свой порт через переменную PORT.

## 3. Первый деплой

Просто **Create Web Service** — Render соберёт образ по `Dockerfile` (ffmpeg,
шрифты и т.д. уже прописаны там) и задеплоит. Первая сборка — 5-10 минут.

После деплоя Render покажет URL вида `https://videobilder.onrender.com`.

## 4. Прописать PUBLIC_BASE_URL

Вернись в **Environment**, впиши `PUBLIC_BASE_URL` = этот URL (без слэша на
конце) → **Save Changes** — Render передеплоит сервис автоматически, при
старте бот сам зарегистрирует вебхук у Telegram (см. логи — строка
`Webhook зарегистрирован: ...`).

## 5. Проверка

```
curl https://videobilder.onrender.com/health
```
должно вернуть `ok`. Дальше пиши боту `/start` в Telegram.

## 6. Free tier: сон и пробуждение

Render free tier усыпляет Web Service после ~15 минут без входящих HTTP-
запросов, следующий запрос (например, апдейт от Telegram при `/start`)
разбудит его — но первое сообщение после сна придёт с задержкой в
несколько десятков секунд, пока контейнер поднимается. Это нормально для
бота "раз в день".

## 7. Ручное включение/выключение

**Settings → Suspend Web Service** (внизу страницы сервиса) — полностью
останавливает инстанс, ничего не тратится, пока не нажмёшь **Resume**.

## Если что-то не работает

- **Бот не отвечает** — Render Dashboard → сервис → **Logs**.
- **RAM/OOM при рендере** — рендерер уже переписан под экономный
  поэтапный режим (см. комментарии в начале `video_generation/renderer.py`),
  но если всё равно не хватает 512 МБ free tier — можно временно перейти на
  платный `Starter` план (2 ГБ RAM, ~$7/мес) только на время генерации, либо
  писать сюда, разберём точечно что жрёт память.
- **Ошибка Google Drive 403** — сервисный аккаунт не добавлен в шаринг папки,
  либо не включён Drive API, либо `GOOGLE_SERVICE_ACCOUNT_JSON` не задан/невалиден.
- **ElevenLabs не работает** — бот автоматически откатится на бесплатный
  Edge TTS (`ru-RU-DmitryNeural`).
