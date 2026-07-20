# videobilder-proxy

Маленький Vercel-прокси только для одного: пробрасывать вызовы Telegram Bot API,
потому что Hugging Face Spaces блокирует исходящие запросы к `api.telegram.org`
(входящие — вебхук от Telegram к самому Space — работают напрямую, без прокси).

## Деплой (2 минуты)

1. Установи Vercel CLI, если ещё нет: `npm install -g vercel`
2. Из этой папки: `vercel --prod`
   (при первом запуске попросит войти/создать аккаунт — можно через GitHub)
3. Vercel даст домен вида `https://videobilder-proxy-xxxx.vercel.app`
4. Полный URL прокси для бота: `https://videobilder-proxy-xxxx.vercel.app/api/tg`
   Это и есть значение `TG_PROXY_URL` в переменных окружения HF Space.

Никаких секретов/переменных окружения самому прокси не нужно — он ничего
не хранит и не знает про токен бота, просто пробрасывает путь как есть
(`/api/tg/bot<TOKEN>/<method>` → `https://api.telegram.org/bot<TOKEN>/<method>`).

## Проверка

```
curl https://videobilder-proxy-xxxx.vercel.app/api/tg/bot123:abc/getMe
```
Если прокси работает, вернётся обычный ответ Telegram Bot API (ошибка о
неверном токене — это нормально, значит запрос дошёл до Telegram).
