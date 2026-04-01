import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import IO, Any

from aiobotocore.session import get_session
from botocore.config import Config
from botocore.response import StreamingBody
from fastapi import UploadFile

from src.app.core.config import settings

# Минимальный размер части S3 multipart — 5 МБ (требование AWS).
# В памяти одновременно живёт ровно один такой чанк на файл.
_MULTIPART_CHUNK_SIZE = 5 * 1024 * 1024  # 5 МБ


class S3Storage:
    def __init__(self) -> None:
        self.session = get_session()
        self.config = {
            "aws_access_key_id": settings.S3_ACCESS_KEY,
            "aws_secret_access_key": settings.S3_SECRET_KEY,
            "endpoint_url": settings.S3_ENDPOINT_URL,
            "region_name": settings.S3_REGION,
        }
        self.s3_config = Config(s3={"addressing_style": "path"})

    @asynccontextmanager
    async def get_client(self) -> AsyncIterator[Any]:
        async with self.session.create_client("s3", config=self.s3_config, **self.config) as client:
            yield client

    @asynccontextmanager
    async def get_file_stream(self, object_key: str) -> AsyncIterator[StreamingBody]:
        """Возвращает стрим"""
        async with self.get_client() as client:
            response = await client.get_object(Bucket=settings.S3_BUCKET_NAME, Key=object_key)
            yield response["Body"]

    async def init_bucket(self) -> None:
        """Создает корзину, если она не существует"""
        async with self.get_client() as client:
            try:
                await client.head_bucket(Bucket=settings.S3_BUCKET_NAME)
            except Exception:
                await client.create_bucket(Bucket=settings.S3_BUCKET_NAME)
                print(f"Bucket '{settings.S3_BUCKET_NAME}' created successfully.")

    async def upload_file(self, file_obj: IO[bytes] | bytes, object_key: str, content_type: str) -> None:
        async with self.get_client() as client:
            await client.put_object(
                Bucket=settings.S3_BUCKET_NAME,
                Key=object_key,
                Body=file_obj,
                ContentType=content_type,
            )

    async def upload_file_multipart(
        self,
        upload: UploadFile,
        object_key: str,
        content_type: str,
    ) -> int:
        """
        Потоковая загрузка файла в S3 через Multipart Upload.

        Читает файл чанками по _MULTIPART_CHUNK_SIZE (5 МБ) —
        в памяти одновременно живёт ровно один чанк.
        """
        async with self.get_client() as client:
            mpu = await client.create_multipart_upload(
                Bucket=settings.S3_BUCKET_NAME,
                Key=object_key,
                ContentType=content_type,
            )
            upload_id: str = mpu["UploadId"]
            parts: list[dict[str, int | Any]] = []
            part_number = 1
            total_bytes = 0

            try:
                while True:
                    chunk = await upload.read(_MULTIPART_CHUNK_SIZE)
                    if not chunk:
                        break

                    resp = await client.upload_part(
                        Bucket=settings.S3_BUCKET_NAME,
                        Key=object_key,
                        UploadId=upload_id,
                        PartNumber=part_number,
                        Body=chunk,
                    )
                    parts.append({"PartNumber": part_number, "ETag": resp["ETag"]})
                    total_bytes += len(chunk)
                    part_number += 1

                if not parts:
                    resp = await client.upload_part(
                        Bucket=settings.S3_BUCKET_NAME,
                        Key=object_key,
                        UploadId=upload_id,
                        PartNumber=1,
                        Body=b"",
                    )
                    parts.append({"PartNumber": 1, "ETag": resp["ETag"]})

                await client.complete_multipart_upload(
                    Bucket=settings.S3_BUCKET_NAME,
                    Key=object_key,
                    UploadId=upload_id,
                    MultipartUpload={"Parts": parts},
                )

            except Exception:
                try:
                    await client.abort_multipart_upload(
                        Bucket=settings.S3_BUCKET_NAME,
                        Key=object_key,
                        UploadId=upload_id,
                    )
                except Exception:  # nosec B105,B110
                    pass
                raise

        return total_bytes

    async def get_presigned_url(
        self, object_key: str, original_filename: str | None = None, expires_in: int = 3600, download: bool = False
    ) -> str:
        params = {"Bucket": settings.S3_BUCKET_NAME, "Key": object_key}

        if original_filename:
            safe_filename = re.sub(r"[^\w\-. ]", "_", original_filename)
            disposition_type = "attachment" if download else "inline"
            content_disposition = f'{disposition_type}; filename="{safe_filename}"'
            params["ResponseContentDisposition"] = content_disposition

            import mimetypes

            content_type, _ = mimetypes.guess_type(original_filename)
            if content_type:
                params["ResponseContentType"] = content_type

        async with self.get_client() as client:
            url: str = await client.generate_presigned_url(
                "get_object",
                Params=params,
                ExpiresIn=expires_in,
            )
            return url

    async def get_file_content(self, object_key: str) -> bytes:
        """Получает содержимое файла из S3"""
        async with self.get_client() as client:
            response = await client.get_object(Bucket=settings.S3_BUCKET_NAME, Key=object_key)
            body = response["Body"]
            content: bytes = await body.read()
            return content

    async def delete_file(self, object_key: str) -> None:
        async with self.get_client() as client:
            await client.delete_object(Bucket=settings.S3_BUCKET_NAME, Key=object_key)


s3_storage = S3Storage()
