// api/tg/[...path].js
//
// Отдельный прокси для videobilder (не завязан на elyon-ai-web).
// HF Spaces блокирует исходящие запросы к api.telegram.org — этот relay
// стоит на Vercel (у него такого ограничения нет) и просто прозрачно
// пробрасывает запрос дальше на Telegram Bot API.
//
// Используется для ВСЕХ вызовов Bot API (sendMessage, sendMediaGroup,
// answerCallbackQuery, deleteMessage, setWebhook, sendVoice и т.п.) —
// это всё небольшие payload'ы (JSON или маленькие multipart-файлы вроде
// превью фонов), укладываются в лимит Vercel 4.5 МБ на тело запроса.
//
// Готовое видео НЕ идёт через этот прокси (см. bot/delivery.py) — оно
// грузится на Google Drive, и в Telegram передаётся прямая ссылка на файл,
// Telegram сам её скачивает напрямую с Google, минуя и прокси, и фаервол HF.
//
// Роут: https://<домен>/api/tg/bot<TOKEN>/<method>  → https://api.telegram.org/bot<TOKEN>/<method>
//       https://<домен>/api/tg/file/bot<TOKEN>/<path> → https://api.telegram.org/file/bot<TOKEN>/<path>

export const config = {
  api: {
    bodyParser: false, // нужен «сырой» body как есть (multipart для файлов, JSON как есть)
  },
};

async function readRawBody(req) {
  const chunks = [];
  for await (const chunk of req) {
    chunks.push(chunk);
  }
  return Buffer.concat(chunks);
}

export default async function handler(req, res) {
  const segments = Array.isArray(req.query.path) ? req.query.path : [req.query.path].filter(Boolean);
  if (segments.length === 0) {
    res.status(400).json({ ok: false, error: "missing path" });
    return;
  }

  const targetUrl = `https://api.telegram.org/${segments.join("/")}`;

  const headers = {};
  const contentType = req.headers["content-type"];
  if (contentType) headers["content-type"] = contentType;

  let body;
  if (req.method !== "GET" && req.method !== "HEAD") {
    body = await readRawBody(req);
  }

  try {
    const upstream = await fetch(targetUrl, {
      method: req.method,
      headers,
      body,
    });

    const buffer = Buffer.from(await upstream.arrayBuffer());
    res.status(upstream.status);
    const upstreamContentType = upstream.headers.get("content-type");
    if (upstreamContentType) res.setHeader("content-type", upstreamContentType);
    res.send(buffer);
  } catch (err) {
    res.status(502).json({ ok: false, error: String(err) });
  }
}
