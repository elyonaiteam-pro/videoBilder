# Деплой videobilder на Hugging Face Spaces

Пошаговая инструкция. Считаем, что Фазы 1-3 кода уже в репозитории (этот файл
сам по себе создаётся вместе с ними).

## 1. Прокси Telegram API (Vercel)

HF Spaces блокирует исходящие запросы к `api.telegram.org` — нужен прокси.

```bash
cd infra/vercel-proxy
npm install -g vercel   # если ещё не установлен
vercel --prod
```

Получишь домен вида `https://videobilder-proxy-xxxx.vercel.app`.
Полный URL для `TG_PROXY_URL` = `https://videobilder-proxy-xxxx.vercel.app/api/tg`
(без слэша на конце).

## 2. Google Drive для доставки готового видео

Готовое видео (5-20 МБ) не проходит через прокси (лимит Vercel 4.5 МБ) — вместо
этого бот заливает его на Google Drive и передаёт в Telegram прямую ссылку.

1. [Google Cloud Console](https://console.cloud.google.com/) → создать проект
   (или использовать существующий) → **APIs & Services → Library** → включить
   **Google Drive API**.
2. **APIs & Services → Credentials → Create Credentials → Service Account**.
   Имя любое, роль не обязательна (права выдаются на уровне папки Drive, см. ниже).
3. У созданного сервисного аккаунта: **Keys → Add Key → Create new key → JSON**.
   Скачается `.json`-файл — его *целиком* содержимое пойдёт в секрет
   `GOOGLE_SERVICE_ACCOUNT_JSON` (весь JSON одной строкой/как есть).
4. В самом JSON-файле есть поле `client_email` (что-то вроде
   `videobilder@project-id.iam.gserviceaccount.com`) — скопируй его.
5. На google.com/drive создай папку (например `videobilder-output`) →
   **Поделиться** → вставь email из шага 4 → выдай роль **Редактор**.
6. Открой эту папку в браузере, из URL вида
   `https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOp` скопируй
   `1AbCdEfGhIjKlMnOp` — это `GOOGLE_DRIVE_FOLDER_ID`.

## 3. Создание Space

1. [huggingface.co/new-space](https://huggingface.co/new-space)
2. Owner: свой аккаунт, Space name: `videobilder`, License: любая,
   **SDK: Docker** (важно — не Gradio/Streamlit), Visibility: **Private**
   (это личный бот с секретами, приватность имеет смысл).
3. После создания — либо `git push` этого репозитория в качестве remote
   Space'а, либо загрузить файлы через веб-интерфейс. Репозиторий уже содержит
   `README.md` с нужным YAML-фронтматтером (`sdk: docker`, `app_port: 7860`),
   Dockerfile HF подхватит автоматически.

```bash
git remote add hf https://huggingface.co/spaces/<username>/videobilder
git push hf main
```

## 4. Секреты Space

**Space → Settings → Variables and secrets → New secret**, добавить как
**Secret** (не Variable — значения не должны светиться в логах):

| Секрет | Значение |
|---|---|
| `TELEGRAM_BOT_TOKEN` | токен от @BotFather |
| `ALLOWED_USER_IDS` | твой Telegram user id |
| `GEMINI_API_KEY` | ключ Gemini |
| `GEMINI_MODEL` | `gemini-3-pro-preview` |
| `ELEVENLABS_API_KEY` | ключ ElevenLabs |
| `ELEVENLABS_VOICE_ID_DMITRIY` | voice_id Dmitriy D. |
| `ELEVENLABS_VOICE_ID_ADAM` | voice_id Adam |
| `TG_PROXY_URL` | URL из шага 1, с `/api/tg` на конце |
| `SPACE_HOST` | `https://<username>-videobilder.hf.space` (без слэша на конце) |
| `WEBHOOK_SECRET` | любая случайная строка (например `python -c "import secrets;print(secrets.token_urlsafe(24))"`) |
| `GOOGLE_DRIVE_FOLDER_ID` | ID папки из шага 2.6 |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | содержимое JSON-ключа из шага 2.3, целиком |

`PORT` можно не задавать — по умолчанию 7860 (то, что нужно HF).

## 5. Проверка

После деплоя Space соберётся (Docker-сборка занимает пару минут — видно в
логах Space). При старте контейнера `bot/main.py` сам зарегистрирует вебхук
у Telegram (`SPACE_HOST` задан → webhook-режим). В логах Space должна быть
строка `Webhook зарегистрирован: https://.../webhook/<secret>`.

Проверить руками:
```
curl https://<username>-videobilder.hf.space/health
```
должно вернуть `ok`.

Дальше просто пиши боту `/start` в Telegram.

## Если что-то не работает

- **Бот не отвечает** — смотри логи Space (Settings → там же вкладка Logs).
  Частая причина — не тот `TG_PROXY_URL` (без `/api/tg` на конце) или
  неверный `WEBHOOK_SECRET`/`SPACE_HOST`.
- **Ошибка Google Drive 403** — сервисный аккаунт не добавлен в шаринг папки
  (шаг 2.5), либо не включён Drive API (шаг 2.1).
- **ElevenLabs не работает** — не страшно, бот сам откатится на бесплатный
  Edge TTS (`ru-RU-DmitryNeural`), сообщение об этом будет в логах.
