from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from .config import settings


class LocalStorage:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def put_file(self, key: str, src: Path) -> None:
        dst = self.root / key
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)

    def local_path(self, key: str) -> Path:
        return self.root / key

    def delete(self, key: str) -> None:
        p = self.root / key
        if p.exists():
            p.unlink()


class S3Storage:
    def __init__(self):
        import boto3

        self.bucket = settings.s3_bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
        )
        self.cache = Path(tempfile.gettempdir()) / "gool-cache"
        self.cache.mkdir(parents=True, exist_ok=True)

    def put_file(self, key: str, src: Path) -> None:
        self.client.upload_file(str(src), self.bucket, key)

    def local_path(self, key: str) -> Path:
        p = self.cache / key
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            self.client.download_file(self.bucket, key, str(p))
        return p

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)
        (self.cache / key).unlink(missing_ok=True)


def _make():
    if settings.storage_backend == "s3" and settings.s3_bucket:
        return S3Storage()
    return LocalStorage(settings.storage_dir / "files")


storage = _make()
