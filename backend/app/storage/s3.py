from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from uuid import UUID

import boto3
from botocore.client import BaseClient
from botocore.config import Config
from botocore.exceptions import ClientError

from app.settings import Settings, get_settings

UPLOAD_URL_TTL = timedelta(minutes=15)
DOWNLOAD_URL_TTL = timedelta(minutes=10)
WAV_CONTENT_TYPE = "audio/wav"
MISSING_OBJECT_CODES = {"404", "NoSuchKey", "NotFound"}
DELETE_BATCH = 1000  # S3 한 번 삭제 요청의 최대 객체 수
# 녹음 보관 기간. 버킷의 수명 주기 규칙(올라간 날부터 365일 뒤 삭제)과 같게 둔다.
RECORDING_RETENTION = timedelta(days=365)


def recording_key(user_id: UUID, session_id: UUID, recording_id: UUID) -> str:
    return f"users/{user_id}/sessions/{session_id}/{recording_id}.wav"


def user_prefix(user_id: UUID) -> str:
    return f"users/{user_id}/"


@dataclass(frozen=True)
class PresignedUrl:
    url: str
    method: str
    headers: dict[str, str]
    expires_at: datetime


@dataclass(frozen=True)
class ObjectInfo:
    size: int
    # S3에 올라간 시각. 수명 주기 규칙은 이 시각부터 센다.
    last_modified: datetime


def make_client(settings: Settings) -> BaseClient:
    missing = [
        name
        for name, value in [
            ("AWS_REGION", settings.aws_region),
            ("AWS_ACCESS_KEY_ID", settings.aws_access_key_id),
            ("AWS_SECRET_ACCESS_KEY", settings.aws_secret_access_key),
        ]
        if value is None
    ]
    if missing:
        raise RuntimeError(f"S3 설정이 없습니다: {', '.join(missing)}")
    return boto3.client(
        "s3",
        region_name=settings.aws_region,
        aws_access_key_id=settings.aws_access_key_id.get_secret_value(),
        aws_secret_access_key=settings.aws_secret_access_key.get_secret_value(),
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "virtual"},
            connect_timeout=5,
            read_timeout=10,
            retries={"max_attempts": 3, "mode": "standard"},
        ),
    )


class RecordingStorage:
    """녹음 버킷에 대한 작업. presigned URL을 만들고, 객체를 확인·읽기·삭제한다."""

    def __init__(self, client: BaseClient, bucket: str) -> None:
        self.client = client
        self.bucket = bucket

    def upload_url(self, key: str, now: datetime | None = None) -> PresignedUrl:
        # Content-Type을 서명에 넣으므로, 앱은 응답의 headers를 그대로 붙여 올려야 한다.
        url = self.client.generate_presigned_url(
            "put_object",
            Params={"Bucket": self.bucket, "Key": key, "ContentType": WAV_CONTENT_TYPE},
            ExpiresIn=int(UPLOAD_URL_TTL.total_seconds()),
        )
        return PresignedUrl(url, "PUT", {"Content-Type": WAV_CONTENT_TYPE}, (now or datetime.now(UTC)) + UPLOAD_URL_TTL)

    def download_url(self, key: str, now: datetime | None = None) -> PresignedUrl:
        url = self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=int(DOWNLOAD_URL_TTL.total_seconds()),
        )
        return PresignedUrl(url, "GET", {}, (now or datetime.now(UTC)) + DOWNLOAD_URL_TTL)

    def info(self, key: str) -> ObjectInfo | None:
        """객체 크기와 올라간 시각. 없으면 None"""
        try:
            response = self.client.head_object(Bucket=self.bucket, Key=key)
        except ClientError as error:
            if error.response["Error"]["Code"] in MISSING_OBJECT_CODES:
                return None
            raise
        return ObjectInfo(response["ContentLength"], response["LastModified"])

    def size(self, key: str) -> int | None:
        """객체 크기(바이트). 없으면 None"""
        info = self.info(key)
        return None if info is None else info.size

    def read_start(self, key: str, length: int) -> bytes:
        """객체 앞부분만 읽는다(파일이 length보다 짧으면 있는 만큼)."""
        response = self.client.get_object(Bucket=self.bucket, Key=key, Range=f"bytes=0-{length - 1}")
        return response["Body"].read()

    def keys_under(self, prefix: str) -> list[str]:
        paginator = self.client.get_paginator("list_objects_v2")
        return [obj["Key"] for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix) for obj in page.get("Contents", [])]

    def delete(self, keys: list[str]) -> int:
        """객체를 지우고 지운 개수를 돌려준다. 하나라도 실패하면 예외를 낸다."""
        keys = sorted(set(keys))
        for start in range(0, len(keys), DELETE_BATCH):
            batch = keys[start : start + DELETE_BATCH]
            response = self.client.delete_objects(
                Bucket=self.bucket, Delete={"Objects": [{"Key": key} for key in batch], "Quiet": True}
            )
            if response.get("Errors"):
                failed = ", ".join(f"{e['Key']}({e['Code']})" for e in response["Errors"])
                raise RuntimeError(f"S3 객체를 지우지 못했습니다: {failed}")
        return len(keys)


def make_storage(settings: Settings) -> RecordingStorage:
    if settings.s3_bucket is None:
        raise RuntimeError("S3 설정이 없습니다: S3_BUCKET")
    return RecordingStorage(make_client(settings), settings.s3_bucket)


@lru_cache
def get_storage() -> RecordingStorage:
    return make_storage(get_settings())
