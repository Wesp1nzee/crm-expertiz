import logging
import mimetypes
import os
import re
import urllib.parse
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import IO, Any, cast

from aiobotocore.session import AioSession, get_session
from botocore.config import Config
from botocore.exceptions import ClientError
from botocore.response import StreamingBody
from fastapi import UploadFile
from types_aiobotocore_s3.client import S3Client
from types_aiobotocore_s3.type_defs import PartTypeDef

from src.app.core.config import settings

logger = logging.getLogger(__name__)

_PRESIGNED_POST_MAX_SIZE = 50 * 1024 * 1024  # 50 МБ
_DOWNLOAD_TTL = 600  # 10 минут
_UPLOAD_TTL = 3600  # 1 час


class S3Storage:
    def __init__(self) -> None:
        self._session: AioSession = get_session()
        self._client: S3Client | None = None
        self._ctx: Any = None
        self._config: dict[str, str | None] = {
            "aws_access_key_id": settings.S3_ACCESS_KEY,
            "aws_secret_access_key": settings.S3_SECRET_KEY,
            "endpoint_url": settings.S3_ENDPOINT_URL,
            "region_name": settings.S3_REGION,
        }
        self._s3_config = Config(signature_version="s3v4", s3={"addressing_style": "path"})
        self._bucket: str = settings.S3_BUCKET_NAME

    async def __aenter__(self) -> S3Storage:
        if self._client is not None:
            logger.warning("S3 client уже инициализирован. Повторный вызов игнорируется.")
            return self

        self._ctx = self._session.create_client("s3", config=self._s3_config, **self._config)
        self._client = cast(S3Client, await self._ctx.__aenter__())
        logger.info("S3 асинхронный клиент успешно создан через Lifespan")
        return self

    async def __aexit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        if self._ctx is not None:
            await self._ctx.__aexit__(exc_type, exc_val, exc_tb)
            self._ctx = None
            self._client = None
            logger.info("S3 асинхронный пул соединений успешно закрыт")

    @property
    def client(self) -> S3Client:
        """Возвращает активного типизированного клиента S3."""
        if self._client is None:
            raise RuntimeError("S3Storage не инициализирован в Lifespan фазе приложения.")
        return self._client

    @property
    def bucket(self) -> str:
        return self._bucket

    @asynccontextmanager
    async def get_client(self) -> AsyncIterator[S3Client]:
        """Для обратной совместимости со старым кодом почтового сервиса."""
        yield self.client

    async def init_bucket(self) -> None:
        """Валидирует бакет на этапе старта приложения."""
        try:
            await self.client.head_bucket(Bucket=self._bucket)
            logger.info("Бакет '%s' успешно валидирован", self._bucket)
        except ClientError as exc:
            error_code = str(exc.response.get("Error", {}).get("Code", ""))
            status_code = int(exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0))

            if error_code in ("404", "NoSuchBucket") or status_code == 404:
                logger.info("Бакет '%s' не найден. Запускаю автоматическое создание...", self._bucket)
                await self.client.create_bucket(Bucket=self._bucket)
                logger.info("Бакет '%s' успешно создан", self._bucket)
            else:
                logger.critical("Критическая ошибка прав/конфигурации S3 для бакета '%s': %s", self._bucket, exc)
                raise
        except Exception as exc:
            logger.critical("Неизвестный системный сбой S3 при старте приложения: %s", exc, exc_info=True)
            raise

    async def upload_file(self, file_obj: IO[bytes] | bytes, object_key: str, content_type: str) -> None:
        """Для почты"""
        await self.client.put_object(
            Bucket=self._bucket,
            Key=object_key,
            Body=file_obj,
            ContentType=content_type,
        )

    async def upload_file_multipart(self, upload: UploadFile, object_key: str, content_type: str) -> int:
        """Загружает файл в S3 с multipart upload для больших файлов. Возвращает размер."""
        upload.file.seek(0, os.SEEK_END)
        size = upload.file.tell()
        upload.file.seek(0)

        extra_args: dict[str, str] = {}
        if content_type:
            extra_args["ContentType"] = content_type

        await self.client.upload_fileobj(
            Bucket=self._bucket,
            Key=object_key,
            Fileobj=upload.file,
            ExtraArgs=extra_args,
        )
        return size

    async def generate_presigned_post(
        self,
        object_key: str,
        content_type: str,
        min_size: int = 1,
        max_size: int = _PRESIGNED_POST_MAX_SIZE,
        expires_in: int = _UPLOAD_TTL,
    ) -> dict[str, Any]:
        """Генерация Presigned Post формы для файлов <= 50 МБ."""
        conditions: list[list[str | int] | dict[str, str]] = [
            {"bucket": self._bucket},
            {"key": object_key},
            {"Content-Type": content_type},
            ["content-length-range", min_size, max_size],
        ]

        result = await self.client.generate_presigned_post(
            Bucket=self._bucket,
            Key=object_key,
            Fields={"Content-Type": content_type},
            Conditions=conditions,
            ExpiresIn=expires_in,
        )
        logger.info("Generated presigned POST URL: %s", result["url"])
        logger.info("Fields: %s", result["fields"])
        return {
            "url": result["url"],
            "fields": dict(result["fields"]),
        }

    async def create_multipart_upload(self, object_key: str, content_type: str) -> dict[str, str]:
        mpu = await self.client.create_multipart_upload(
            Bucket=self._bucket,
            Key=object_key,
            ContentType=content_type,
        )
        return {"upload_id": str(mpu["UploadId"]), "key": object_key}

    async def generate_presigned_url_upload_part(self, object_key: str, upload_id: str, part_number: int, expires_in: int = _UPLOAD_TTL) -> str:
        url: str = await self.client.generate_presigned_url(
            "upload_part",
            Params={
                "Bucket": self._bucket,
                "Key": object_key,
                "UploadId": upload_id,
                "PartNumber": part_number,
            },
            ExpiresIn=expires_in,
        )
        return url

    async def complete_multipart_upload(self, object_key: str, upload_id: str, parts: Sequence[PartTypeDef]) -> dict[str, str]:
        """Финализирует Multipart Upload сборку на стороне S3."""
        result = await self.client.complete_multipart_upload(
            Bucket=self._bucket,
            Key=object_key,
            UploadId=upload_id,
            MultipartUpload={"Parts": list(parts)},
        )
        return {
            "key": object_key,
            "location": result.get("Location", ""),
            "etag": result.get("ETag", ""),
        }

    async def abort_multipart_upload(self, object_key: str, upload_id: str) -> None:
        await self.client.abort_multipart_upload(
            Bucket=self._bucket,
            Key=object_key,
            UploadId=upload_id,
        )

    async def list_multipart_uploads(self) -> list[dict[str, Any]]:
        result = await self.client.list_multipart_uploads(Bucket=self._bucket)
        uploads: list[dict[str, Any]] = []
        raw_uploads = result.get("Uploads", [])
        if raw_uploads:
            for u in raw_uploads:
                uploads.append(
                    {
                        "key": u.get("Key", ""),
                        "upload_id": u.get("UploadId", ""),
                        "initiated": u.get("Initiated"),
                    }
                )
        return uploads

    @staticmethod
    def _sanitize_filename[S: str](filename: S) -> str:
        """Очищает имя файла от инъекций управляющих символов."""
        sanitized = re.sub(r'[\x00-\x1f\x7f"\\]', "_", filename)
        return sanitized.strip() or "file"

    async def generate_presigned_get_url(
        self,
        object_key: str,
        original_filename: str | None = None,
        download: bool = False,
        expires_in: int = _DOWNLOAD_TTL,
    ) -> str:
        params: dict[str, Any] = {
            "Bucket": self._bucket,
            "Key": object_key,
        }

        if original_filename:
            safe = self._sanitize_filename(original_filename)
            encoded = urllib.parse.quote(safe, safe="")
            disposition_type = "attachment" if download else "inline"

            ascii_fallback = re.sub(r"[^\x20-\x7e]", "_", safe)
            params["ResponseContentDisposition"] = f"{disposition_type}; filename=\"{ascii_fallback}\"; filename*=UTF-8''{encoded}"

            content_type, _ = mimetypes.guess_type(original_filename)
            if content_type:
                params["ResponseContentType"] = content_type

        url: str = await self.client.generate_presigned_url(
            "get_object",
            Params=params,
            ExpiresIn=expires_in,
        )
        return url

    async def get_presigned_url(
        self,
        object_key: str,
        original_filename: str | None = None,
        expires_in: int = 3600,
        download: bool = False,
    ) -> str:
        return await self.generate_presigned_get_url(
            object_key=object_key,
            original_filename=original_filename,
            download=download,
            expires_in=expires_in,
        )

    @asynccontextmanager
    async def get_file_stream(self, object_key: str) -> AsyncIterator[StreamingBody]:
        response = await self.client.get_object(Bucket=self._bucket, Key=object_key)
        yield response["Body"]

    async def get_file_content(self, object_key: str) -> bytes:
        response = await self.client.get_object(Bucket=self._bucket, Key=object_key)
        async with response["Body"] as stream:
            content: bytes = await stream.read()
        return content

    async def delete_file(self, object_key: str) -> None:
        await self.client.delete_object(Bucket=self._bucket, Key=object_key)

    async def head_object(self, object_key: str) -> dict[str, Any] | None:
        try:
            response = await self.client.head_object(Bucket=self._bucket, Key=object_key)
            return dict(response)
        except ClientError as exc:
            if int(exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0)) == 404:
                return None
            raise


s3_storage = S3Storage()
