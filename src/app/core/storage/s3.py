import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from aiobotocore.session import get_session
from botocore.config import Config

from src.app.core.config import settings


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

    async def init_bucket(self) -> None:
        """Создает корзину, если она не существует"""
        async with self.get_client() as client:
            try:
                await client.head_bucket(Bucket=settings.S3_BUCKET_NAME)
            except Exception:
                await client.create_bucket(Bucket=settings.S3_BUCKET_NAME)
                print(f"Bucket '{settings.S3_BUCKET_NAME}' created successfully.")

    async def upload_file(self, file_data: bytes, object_key: str, content_type: str) -> None:
        async with self.get_client() as client:
            await client.put_object(
                Bucket=settings.S3_BUCKET_NAME,
                Key=object_key,
                Body=file_data,
                ContentType=content_type,
            )

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
