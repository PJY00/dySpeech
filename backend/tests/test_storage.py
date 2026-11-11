"""S3 계층을 moto로 확인한다. 실제 AWS에는 접속하지 않는다."""

import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest
import requests

from app.settings import Settings
from app.storage.s3 import (
    DELETE_BATCH,
    RecordingStorage,
    make_client,
    make_storage,
    recording_key,
    user_prefix,
)

REGION = "ap-northeast-2"
BUCKET = "dyspeech-test-recordings"  # conftest의 storage fixture와 같은 버킷
NOW = datetime(2026, 11, 20, 9, 15, tzinfo=UTC)


def s3_settings(**fields) -> Settings:
    values = {"aws_region": REGION, "aws_access_key_id": "testing", "aws_secret_access_key": "testing", "s3_bucket": BUCKET}
    return Settings(_env_file=None, **values | fields)


def put(storage: RecordingStorage, key: str, body: bytes = b"RIFF\x00\x00\x00\x00WAVE") -> None:
    storage.client.put_object(Bucket=storage.bucket, Key=key, Body=body)


def test_recording_key_layout():
    user_id, session_id, recording_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    assert recording_key(user_id, session_id, recording_id) == f"users/{user_id}/sessions/{session_id}/{recording_id}.wav"
    assert recording_key(user_id, session_id, recording_id).startswith(user_prefix(user_id))


def test_upload_url_signs_content_type_and_expires_in_15_minutes(storage):
    upload = storage.upload_url("users/a/sessions/b/c.wav", now=NOW)
    url = urlparse(upload.url)
    query = parse_qs(url.query)

    assert url.hostname == f"{BUCKET}.s3.{REGION}.amazonaws.com"
    assert query["X-Amz-Expires"] == ["900"]
    assert "content-type" in query["X-Amz-SignedHeaders"][0].split(";")
    assert (upload.method, upload.headers) == ("PUT", {"Content-Type": "audio/wav"})
    assert upload.expires_at == NOW + timedelta(minutes=15)


def test_upload_then_check_and_download(storage):
    key = "users/a/sessions/b/c.wav"
    body = b"RIFF" + bytes(range(256)) * 4
    upload = storage.upload_url(key)

    assert requests.put(upload.url, data=body, headers=upload.headers).status_code == 200
    assert storage.size(key) == len(body)
    assert storage.read_start(key, 12) == body[:12]
    assert storage.read_start(key, 10_000) == body

    download = storage.download_url(key, now=NOW)
    assert parse_qs(urlparse(download.url).query)["X-Amz-Expires"] == ["600"]
    assert download.expires_at == NOW + timedelta(minutes=10)
    assert requests.get(download.url).content == body


def test_size_of_missing_object_is_none(storage):
    assert storage.size("users/a/sessions/b/missing.wav") is None


def test_list_and_delete_under_user_prefix(storage):
    mine = [f"users/me/sessions/s/{i}.wav" for i in range(3)]
    other = "users/other/sessions/s/0.wav"
    for key in [*mine, other]:
        put(storage, key)

    assert sorted(storage.keys_under("users/me/")) == mine
    assert storage.delete(mine + mine[:1]) == 3
    assert storage.keys_under("users/me/") == []
    assert storage.keys_under("users/other/") == [other]


def test_delete_works_beyond_one_batch(storage):
    keys = [f"users/many/{i}.wav" for i in range(DELETE_BATCH + 5)]
    for key in keys:
        put(storage, key, b"x")

    assert storage.delete(keys) == len(keys)
    assert storage.keys_under("users/many/") == []


@pytest.mark.parametrize(
    ("fields", "missing"),
    [
        ({"aws_region": None}, "AWS_REGION"),
        ({"aws_secret_access_key": None}, "AWS_SECRET_ACCESS_KEY"),
        ({"s3_bucket": None}, "S3_BUCKET"),
    ],
)
def test_storage_needs_settings(fields, missing):
    with pytest.raises(RuntimeError, match=missing):
        make_storage(s3_settings(**fields))


def test_client_uses_region_and_signature_v4():
    client = make_client(s3_settings())

    assert client.meta.region_name == REGION
    assert client.meta.config.signature_version == "s3v4"
