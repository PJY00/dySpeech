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
    """
    녹음 하나의 S3 저장 위치(키)를 정함
    입력: user_id (사용자 ID), session_id (세션 ID), recording_id (녹음 ID)
    출력: S3 키 "users/{user_id}/sessions/{session_id}/{recording_id}.wav"
    알고리즘
    1. 세 ID로 경로를 이음
    """
    return f"users/{user_id}/sessions/{session_id}/{recording_id}.wav"


def user_prefix(user_id: UUID) -> str:
    """
    사용자 한 명의 녹음이 모두 들어 있는 S3 키 접두사를 정함
    입력: user_id (사용자 ID)
    출력: 접두사 "users/{user_id}/"
    알고리즘
    1. 사용자 ID로 접두사를 이음
    """
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
    """
    설정의 AWS 값으로 S3 클라이언트를 만들어 돌려줌
    입력: settings (서버 설정: AWS_REGION, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY)
    출력: boto3 S3 클라이언트 (서명 s3v4, 가상 호스트 주소 방식, 연결 제한 5초, 읽기 제한 10초, standard 방식으로 최대 3번 시도). 값이 빠지면 그 이름을 담은 RuntimeError
    알고리즘
    1. 세 AWS 값 중 빠진 것의 이름을 모아, 있으면 RuntimeError("S3 설정이 없습니다: …")를 일으킴
    2. 지역·키와 위 설정으로 클라이언트를 만듦
    """
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
        """
        S3 클라이언트와 버킷으로 녹음 저장소를 초기화함
        입력: client (boto3 S3 클라이언트), bucket (녹음 버킷 이름)
        출력: 없음
        알고리즘
        1. 두 값을 저장함
        """
        self.client = client
        self.bucket = bucket

    def upload_url(self, key: str, now: datetime | None = None) -> PresignedUrl:
        """
        녹음을 올릴 15분짜리 presigned PUT URL을 만들어 돌려줌
        입력: key (S3 키), now (만료 시각을 셀 기준 시각, 없으면 지금 UTC)
        출력: presigned URL(url, method "PUT", headers {"Content-Type": "audio/wav"}, expires_at = now + 15분)
        알고리즘
        1. Content-Type audio/wav를 서명에 넣어 put_object URL을 만듦(유효 900초). 그래서 앱은 응답의 headers를 그대로 붙여 올려야 함
        2. 만료 시각을 now + 15분으로 계산해 함께 돌려줌
        """
        # Content-Type을 서명에 넣으므로, 앱은 응답의 headers를 그대로 붙여 올려야 한다.
        url = self.client.generate_presigned_url(
            "put_object",
            Params={"Bucket": self.bucket, "Key": key, "ContentType": WAV_CONTENT_TYPE},
            ExpiresIn=int(UPLOAD_URL_TTL.total_seconds()),
        )
        return PresignedUrl(url, "PUT", {"Content-Type": WAV_CONTENT_TYPE}, (now or datetime.now(UTC)) + UPLOAD_URL_TTL)

    def download_url(self, key: str, now: datetime | None = None) -> PresignedUrl:
        """
        녹음을 내려받을 10분짜리 presigned GET URL을 만들어 돌려줌
        입력: key (S3 키), now (만료 시각을 셀 기준 시각, 없으면 지금 UTC)
        출력: presigned URL(url, method "GET", headers 빈 사전, expires_at = now + 10분)
        알고리즘
        1. get_object URL을 만듦(유효 600초)
        2. 만료 시각을 now + 10분으로 계산해 함께 돌려줌
        """
        url = self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=int(DOWNLOAD_URL_TTL.total_seconds()),
        )
        return PresignedUrl(url, "GET", {}, (now or datetime.now(UTC)) + DOWNLOAD_URL_TTL)

    def info(self, key: str) -> ObjectInfo | None:
        """
        S3 객체의 크기와 올라간 시각을 읽음
        입력: key (S3 키)
        출력: 객체 정보(size 바이트, last_modified 올라간 시각). 객체가 없으면 None. 그 밖의 S3 오류는 그대로 일으킴
        알고리즘
        1. head_object로 객체 정보를 읽음
        2. 오류 코드가 404, NoSuchKey, NotFound 중 하나면 None을 돌려주고, 아니면 오류를 다시 일으킴
        """
        try:
            response = self.client.head_object(Bucket=self.bucket, Key=key)
        except ClientError as error:
            if error.response["Error"]["Code"] in MISSING_OBJECT_CODES:
                return None
            raise
        return ObjectInfo(response["ContentLength"], response["LastModified"])

    def size(self, key: str) -> int | None:
        """
        S3 객체 크기를 읽음
        입력: key (S3 키)
        출력: 객체 크기(바이트). 없으면 None
        알고리즘
        1. info로 읽어 size만 돌려줌
        """
        info = self.info(key)
        return None if info is None else info.size

    def read(self, key: str) -> bytes:
        """
        서버 지표 계산을 위해 S3 객체 전체를 읽음
        입력: key (S3 키)
        출력: 객체 내용(바이트). 녹음 하나는 최대 약 12 MB
        알고리즘
        1. get_object로 받아 본문을 모두 읽음
        """
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def read_start(self, key: str, length: int) -> bytes:
        """
        S3 객체의 앞부분만 읽음
        입력: key (S3 키), length (읽을 길이, 바이트)
        출력: 앞 length바이트(파일이 length보다 짧으면 있는 만큼)
        알고리즘
        1. 범위 "bytes=0-(length − 1)"로 get_object를 요청해 본문을 읽음
        """
        response = self.client.get_object(Bucket=self.bucket, Key=key, Range=f"bytes=0-{length - 1}")
        return response["Body"].read()

    def keys_under(self, prefix: str) -> list[str]:
        """
        접두사 아래의 S3 키를 모두 모음
        입력: prefix (S3 키 접두사, 예: users/{user_id}/)
        출력: 키 목록
        알고리즘
        1. list_objects_v2를 페이지마다 차례로 받아 모든 객체의 키를 이음
        """
        paginator = self.client.get_paginator("list_objects_v2")
        return [obj["Key"] for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix) for obj in page.get("Contents", [])]

    def delete(self, keys: list[str]) -> int:
        """
        S3 객체를 지우고 지운 개수를 돌려줌
        입력: keys (지울 S3 키 목록, 겹쳐도 됨)
        출력: 지운 개수(겹친 키를 뺀 요청 키 수). S3에서 객체를 지움. 하나라도 실패하면 실패한 키와 코드를 담은 RuntimeError를 일으킴
        알고리즘
        1. 키의 겹침을 없애고 정렬함
        2. S3 한 번 삭제 요청의 최대 객체 수인 1,000개씩 delete_objects(Quiet)로 지움
        3. 응답에 Errors가 있으면 실패한 키와 코드를 담아 RuntimeError를 일으킴
        """
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
    """
    설정으로 녹음 저장소를 만들어 돌려줌
    입력: settings (서버 설정: S3_BUCKET과 make_client가 읽는 AWS 값)
    출력: 녹음 저장소(RecordingStorage). S3_BUCKET이 없으면 RuntimeError
    알고리즘
    1. s3_bucket이 없으면 RuntimeError("S3 설정이 없습니다: S3_BUCKET")를 일으킴
    2. make_client로 만든 클라이언트와 버킷으로 저장소를 만듦
    """
    if settings.s3_bucket is None:
        raise RuntimeError("S3 설정이 없습니다: S3_BUCKET")
    return RecordingStorage(make_client(settings), settings.s3_bucket)


@lru_cache
def get_storage() -> RecordingStorage:
    """
    설정으로 녹음 저장소를 한 번만 만들어 다시 씀
    입력: 없음 (환경 변수 S3_BUCKET, AWS_REGION, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, app/settings.py)
    출력: 녹음 저장소(lru_cache로 하나만 둠)
    알고리즘
    1. get_settings()의 설정으로 make_storage를 부름. 처음 부를 때만 만들고 이후에는 같은 저장소를 돌려줌
    """
    return make_storage(get_settings())
