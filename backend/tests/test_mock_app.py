"""mock_app을 테스트 클라이언트(로컬 테스트 DB)와 moto S3로 끝까지 돌린다."""

import uuid
from functools import partial
from urllib.parse import unquote, urlparse

import pytest
from botocore.exceptions import ClientError
from conftest import TEST_APP_KEY
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db.models import GameRunRow, MeasurementRow, RecordingRow, SessionRow, UserRow
from app.main import app
from app.storage.wav import check_wav, parse_wav_header
from tools import mock_app
from tools.mock_app import Api, Reply


class AppHttp:
    """테스트 클라이언트로 서버에 보낸다. 앱 키는 mock_app이 직접 붙인다."""

    def __init__(self, client) -> None:
        self.client = client

    def send(self, method, url, headers, body):
        response = self.client.request(method, url, headers=headers, content=body)
        return Reply(response.status_code, response.content)


class MotoFiles:
    """presigned URL로 오는 S3 요청을 moto 버킷에 넣고 뺀다. Content-Type이 서명과 다르면 S3처럼 403이다."""

    def __init__(self, storage, put_status: int | None = None) -> None:
        self.storage = storage
        self.put_status = put_status
        self.puts = 0

    def send(self, method, url, headers, body):
        key = unquote(urlparse(url).path.lstrip("/"))
        if method == "PUT":
            self.puts += 1
            if self.put_status is not None or headers.get("Content-Type") != "audio/wav":
                return Reply(self.put_status or 403, b"")
            self.storage.client.put_object(Bucket=self.storage.bucket, Key=key, Body=body, ContentType="audio/wav")
            return Reply(200, b"")
        try:
            return Reply(200, self.storage.client.get_object(Bucket=self.storage.bucket, Key=key)["Body"].read())
        except ClientError:
            return Reply(404, b"")


def count(db, model) -> int:
    db.expire_all()
    return db.scalar(select(func.count()).select_from(model))


def run(flow_client, db, storage, tmp_path, files=None, argv=()):
    lines: list[str] = []
    code = mock_app.main(
        list(argv),
        # flow_client가 DB·S3를 테스트용으로 바꿔 둔 앱에, 기본 헤더가 없는 클라이언트로 보낸다.
        api=Api("", TEST_APP_KEY, AppHttp(TestClient(app))),
        files=files or MotoFiles(storage),
        cleaner=partial(mock_app.clean_up, db=db, storage=storage, out=lines.append),
        ids_dir=tmp_path,
        out=lines.append,
    )
    return code, lines


def test_full_flow_passes_and_cleans_up(flow_client, db, storage, tmp_path):
    code, lines = run(flow_client, db, storage, tmp_path)

    assert code == 0, "\n".join(lines)
    assert not [line for line in lines if line.startswith("실패")]
    for step in ("올리지 않은 녹음은 409", "스테레오 WAV는 422", "complete 202", "열람 URL로 받은 파일이 올린 파일과 같음"):
        assert f"통과  {step}" in lines
    assert "통과  정리 후 남은 것: DB 행 0개, S3 객체 0개" in lines
    assert all(count(db, model) == 0 for model in (UserRow, SessionRow, RecordingRow, MeasurementRow, GameRunRow))
    assert storage.keys_under("users/") == []
    ids_file = next(tmp_path.glob("*.json"))
    assert ids_file.read_text(encoding="utf-8").count("users/") == 9  # 훈련 녹음 6개 + 시험 소리 3개


def test_keep_leaves_data_for_inspection(flow_client, db, storage, tmp_path):
    code, lines = run(flow_client, db, storage, tmp_path, argv=["--keep"])

    assert code == 0
    assert count(db, SessionRow) == 2
    assert count(db, RecordingRow) == 9
    assert len(storage.keys_under("users/")) == 9
    assert any("--keep" in line for line in lines)


def test_failed_step_stops_and_still_cleans_up(flow_client, db, storage, tmp_path):
    files = MotoFiles(storage, put_status=403)

    code, lines = run(flow_client, db, storage, tmp_path, files=files)

    assert code == 1
    assert files.puts == 1
    assert "실패  S3 올리기: calibration_glide: HTTP 403" in lines
    assert "앞 단계가 실패해 나머지는 건너뜁니다" in lines
    assert count(db, UserRow) == 0


def test_output_has_no_app_key_or_urls(flow_client, db, storage, tmp_path):
    _, lines = run(flow_client, db, storage, tmp_path)

    text = "\n".join(lines)
    assert TEST_APP_KEY not in text
    assert "X-Amz-" not in text and "amazonaws.com" not in text


@pytest.mark.parametrize(("channels", "rate"), [(1, 48000), (2, 48000), (1, 16000)])
def test_make_wav_writes_16_bit_pcm(channels, rate):
    data = mock_app.make_wav(0.1, rate=rate, channels=channels, tone_hz=150, glide_to_hz=300, noise=0.01)

    fmt = parse_wav_header(data)
    assert (fmt.format_code, fmt.channels, fmt.sample_rate, fmt.bits_per_sample) == (1, channels, rate, 16)
    assert len(data) == 44 + round(0.1 * rate) * channels * 2
    assert (check_wav(data, rate) is None) == (channels == 1)
    assert data == mock_app.make_wav(0.1, rate=rate, channels=channels, tone_hz=150, glide_to_hz=300, noise=0.01)


def test_missing_app_key_stops_before_sending(monkeypatch, tmp_path, capsys):
    from app.settings import Settings

    monkeypatch.setattr("app.settings.get_settings", lambda: Settings(_env_file=None))

    assert mock_app.main([], ids_dir=tmp_path) == 2
    assert "APP_API_KEY가 없습니다" in capsys.readouterr().err
    assert not list(tmp_path.iterdir())


def test_ids_are_uuid_v4():
    app = mock_app.MockApp(Api("", "k", None), None, None, None)

    assert {uuid.UUID(value).version for value in (app.user_id, app.training_id, app.agc_id)} == {4}
