# Деплой videobilder на Google Cloud Run

Пошагово. Сервисный аккаунт `videobilder@elyon-ai-by-elyon-team.iam.gserviceaccount.com`
уже создан — используем его и для Drive, и как identity самого Cloud Run сервиса
(тогда ключ JSON нигде не хранится, см. `bot/delivery.py`).

## 1. Включить нужные API

В [Google Cloud Console](https://console.cloud.google.com/) → выбрать проект
`elyon-ai-by-elyon-team` → **APIs & Services → Library** → включить по очереди:
- **Cloud Run Admin API**
- **Artifact Registry API** (для хранения собранного образа)
- **Google Drive API** (для доставки видео)
- **Cloud Build API** (если деплоить через `--source .`, см. шаг 3)

## 2. Дать сервисному аккаунту нужные роли

**IAM & Admin → IAM** → найти `videobilder@...` → карандаш (Edit) → **Add another role**:
- `Cloud Run Developer` (или `Editor`, если не хочется настраивать точечно)

Также нужна отдельная роль **для пользователя, который деплоит** (твой обычный
аккаунт), если её ещё нет: `Cloud Run Admin` + `Service Account User`.

## 3. Google Drive — расшарить папку

1. На [drive.google.com](https://drive.google.com) создай папку, например
   `videobilder-output`.
2. Поделиться → вставить `videobilder@elyon-ai-by-elyon-team.iam.gserviceaccount.com`
   → роль **Редактор**.
3. Открой папку в браузере, из URL `.../folders/1AbCdEfGhIjKlMnOp` скопируй
   `1AbCdEfGhIjKlMnOp` — это `GOOGLE_DRIVE_FOLDER_ID`.

## 4. Первый деплой (gcloud CLI)

Установи [gcloud CLI](https://cloud.google.com/sdk/docs/install), если ещё нет,
затем:

- Сгенерировать случайный `WEBHOOK_SECRET`:
  `python -c "import secrets;print(secrets.token_urlsafe(24))"`
- `GOOGLE_SERVICE_ACCOUNT_JSON` **не задаём** — Cloud Run использует identity
  сервиса напрямую (Application Default Credentials).
- `PUBLIC_BASE_URL` тоже пока не задаём — Cloud Run ещё не знает свой URL.
- Нужен именно `--allow-unauthenticated` — безопасность обеспечивает
  секретный путь в `WEBHOOK_SECRET`, а не блокировка на уровне сервиса
  (иначе Telegram не сможет достучаться до вебхука).
- **Ни один из параметров ниже не подставляй реальными значениями прямо в
  команду, если планируешь коммитить/публиковать её куда-либо** — здесь для
  примера плейсхолдеры, реальные значения бери из своего `.env`/секретницы.

```powershell
cd E:\Projects\videobilder-main
gcloud auth login
gcloud config set project elyon-ai-by-elyon-team

gcloud run deploy videobilder `
  --source . `
  --region europe-west1 `
  --service-account videobilder@elyon-ai-by-elyon-team.iam.gserviceaccount.com `
  --allow-unauthenticated `
  --min-instances 0 --max-instances 1 `
  --memory 2Gi --cpu 2 --timeout 300 `
  --set-env-vars "TELEGRAM_BOT_TOKEN=...,ALLOWED_USER_IDS=...,GEMINI_API_KEY=...,GEMINI_MODEL=gemini-3-pro-preview,ELEVENLABS_API_KEY=...,ELEVENLABS_VOICE_ID_DMITRIY=...,ELEVENLABS_VOICE_ID_ADAM=...,GOOGLE_DRIVE_FOLDER_ID=...,WEBHOOK_SECRET=..."
```

После сборки (пара минут — компилируется образ через Cloud Build) команда
выведет URL вида `https://videobilder-xxxxxxxxxx-ew.a.run.app`.

## 5. Прописать PUBLIC_BASE_URL и передеплоить

```powershell
gcloud run services update videobilder `
  --region europe-west1 `
  --set-env-vars "PUBLIC_BASE_URL=https://videobilder-xxxxxxxxxx-ew.a.run.app"
```

Это создаст новую ревизию, контейнер перезапустится и на старте сам
зарегистрирует вебхук у Telegram (см. логи — там будет строка
`Webhook зарегистрирован: ...`).

## 6. Проверка

```powershell
curl https://videobilder-xxxxxxxxxx-ew.a.run.app/health
```
должно вернуть `ok`. Дальше просто пиши боту `/start` в Telegram.

## 7. Ручное включение/выключение бота

Cloud Run и так не тратит ресурсы, пока никто не пишет боту (масштабируется
до нуля) — но если хочешь прямо гарантированно "выключить" (например, чтобы
случайно не сгенерировался ролик):

```powershell
# Выключить — сервис перестаёт принимать запросы
gcloud run services update videobilder --region europe-west1 --max-instances 0

# Включить обратно
gcloud run services update videobilder --region europe-west1 --max-instances 1
```

## Переменные окружения — сводная таблица

| Переменная | Значение |
|---|---|
| `TELEGRAM_BOT_TOKEN` | токен от @BotFather |
| `ALLOWED_USER_IDS` | твой Telegram user id |
| `GEMINI_API_KEY` | ключ Gemini |
| `GEMINI_MODEL` | `gemini-3-pro-preview` |
| `ELEVENLABS_API_KEY` | ключ ElevenLabs |
| `ELEVENLABS_VOICE_ID_DMITRIY` | voice_id Dmitriy D. |
| `ELEVENLABS_VOICE_ID_ADAM` | voice_id Adam |
| `GOOGLE_DRIVE_FOLDER_ID` | ID папки из шага 3 |
| `PUBLIC_BASE_URL` | URL сервиса (прописывается ПОСЛЕ первого деплоя, шаг 5) |
| `WEBHOOK_SECRET` | случайная строка |
| `TG_PROXY_URL` | НЕ задаём — Cloud Run не блокирует Telegram напрямую |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | НЕ задаём — используется identity сервиса |

## Если что-то не работает

- **Бот не отвечает** — смотри логи: `gcloud run services logs read videobilder --region europe-west1`
- **Ошибка Google Drive 403** — сервисный аккаунт не добавлен в шаринг папки
  (шаг 3), либо не включён Drive API (шаг 1), либо забыли `--service-account`
  при деплое (шаг 4).
- **ffmpeg падает / рендер не удаётся** — бот всё равно пришлёт сценарий и
  голосовую дорожку текстом вместо видео, ошибка попадёт в логи Cloud Run.
- **ElevenLabs не работает** — не страшно, бот автоматически откатится на
  бесплатный Edge TTS (`ru-RU-DmitryNeural`).
