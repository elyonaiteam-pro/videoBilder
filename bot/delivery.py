"""
bot/delivery.py — доставка готового видео через Google Drive.

Почему не напрямую в Telegram: итоговое видео (5-20 МБ) не должно идти через
маленькие API-прокси (лимиты тела запроса на serverless-платформах). Вместо
этого бот заливает видео на Google Drive, делает его доступным по прямой
ссылке, и передаёт в sendVideo не байты, а САМУ ССЫЛКУ — Telegram сам
скачивает файл с Google Drive.

Аутентификация — два варианта, оба поддержаны одновременно:
  1) GOOGLE_SERVICE_ACCOUNT_JSON задан (локальный запуск/Windows) — используем
     JSON-ключ явно.
  2) GOOGLE_SERVICE_ACCOUNT_JSON пуст (Cloud Run) — используем Application
     Default Credentials: сервисный аккаунт просто ПРИВЯЗАН к самому Cloud Run
     сервису (--service-account при деплое), никакой ключ нигде не хранится,
     Google выдаёт временные токены автоматически. Это безопаснее и не требует
     секрета вообще — см. README_CLOUDRUN_DEPLOY.md.

Настройка (см. README_CLOUDRUN_DEPLOY.md):
  1) Сервисный аккаунт в Google Cloud (уже создан — videobilder@...).
  2) Включить Google Drive API в проекте.
  3) Создать папку на Google Drive, расшарить её на email сервисного аккаунта
     (Editor), ID папки — в GOOGLE_DRIVE_FOLDER_ID.
"""
import asyncio
import json
import logging
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from config.settings import Settings

logger = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/drive.file"]


class GoogleDriveDelivery:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._service = None

    @property
    def enabled(self) -> bool:
        # На Cloud Run включено всегда (ADC через привязанный сервисный аккаунт),
        # локально — только если явно передан ключ.
        return True

    def _get_service(self):
        if self._service is None:
            if self.settings.google_service_account_json:
                info = json.loads(self.settings.google_service_account_json)
                creds = service_account.Credentials.from_service_account_info(info, scopes=_SCOPES)
                logger.info("Google Drive: используется явный service account JSON")
            else:
                import google.auth

                creds, _ = google.auth.default(scopes=_SCOPES)
                logger.info("Google Drive: используются Application Default Credentials")
            self._service = build("drive", "v3", credentials=creds, cache_discovery=False)
        return self._service

    async def upload_video(self, path: Path) -> tuple[str, str]:
        """Загружает видео на Drive, делает публично доступным по ссылке.

        Возвращает (direct_download_url, file_id) — file_id нужен для
        последующей очистки через delete().
        """
        return await asyncio.to_thread(self._upload_sync, path)

    def _upload_sync(self, path: Path) -> tuple[str, str]:
        service = self._get_service()
        body: dict = {"name": path.name}
        if self.settings.google_drive_folder_id:
            body["parents"] = [self.settings.google_drive_folder_id]

        media = MediaFileUpload(str(path), mimetype="video/mp4", resumable=False)
        file = service.files().create(body=body, media_body=media, fields="id").execute()
        file_id = file["id"]

        # Открываем доступ по ссылке (anyone with the link, read-only) —
        # иначе Telegram не сможет скачать файл со своих серверов.
        service.permissions().create(
            fileId=file_id, body={"role": "reader", "type": "anyone"}
        ).execute()

        url = f"https://drive.google.com/uc?export=download&id={file_id}"
        logger.info("Uploaded %s to Google Drive: %s", path.name, url)
        return url, file_id

    async def delete(self, file_id: str) -> None:
        """Убирает файл с Drive после того, как Telegram его успешно забрал —
        не обязательно, но не даёт Drive засоряться (бот используется раз в день)."""
        try:
            await asyncio.to_thread(lambda: self._get_service().files().delete(fileId=file_id).execute())
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to delete Drive file %s: %s", file_id, exc)
