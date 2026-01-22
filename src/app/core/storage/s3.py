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

    async def get_download_url(self, object_key: str, expires_in: int = 3600) -> str:
        async with self.get_client() as client:
            url: str = await client.generate_presigned_url(
                "get_object",
                Params={"Bucket": settings.S3_BUCKET_NAME, "Key": object_key},
                ExpiresIn=expires_in,
            )
            return url

    async def delete_file(self, object_key: str) -> None:
        async with self.get_client() as client:
            await client.delete_object(Bucket=settings.S3_BUCKET_NAME, Key=object_key)


s3_storage = S3Storage()
