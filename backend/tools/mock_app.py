"""앱 흉내: 합성 WAV로 사용자 등록부터 진전도 조회까지 서버에 실제로 보내 본다.

backend 폴더에서 실행한다.
    python -m tools.mock_app                                   로컬 서버(http://127.0.0.1:8000)
    python -m tools.mock_app --base-url https://서버주소        다른 서버
    python -m tools.mock_app --keep                            만든 데이터를 지우지 않는다

- 앱 키는 .env(또는 환경 변수)의 APP_API_KEY를 쓴다.
- 만든 ID는 저장소 루트의 data/mock_app/에 먼저 기록하고, 끝나면 정리 스크립트(tools.cleanup)로 지운다.
  정리는 .env(또는 환경 변수)의 DATABASE_URL·AWS 값으로 한다. 서버와 같은 DB·버킷을 가리켜야 한다.
- 일부러 틀린 요청도 보낸다: 올리지 않은 녹음(409), 스테레오 WAV(422), 앱 키 없음(401).
- 측정 녹음은 정답을 아는 합성 신호(fixtures/synthetic)다. 서버 처리가 끝나기를 기다린 뒤 서버 지표가 정답 허용 범위 안인지 본다.
- 앱 키, presigned URL은 출력하지 않는다(URL 안에 접근 키 ID가 들어 있다).
"""

import argparse
import io
import json
import sys
import time
import uuid
import wave
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import numpy as np

from fixtures.synthetic import generate as synthetic

REPO_ROOT = Path(__file__).resolve().parents[2]
IDS_DIR = REPO_ROOT / "data" / "mock_app"
DEFAULT_BASE_URL = "http://127.0.0.1:8000"
RATE = 48000
DEFAULT_TIMEOUT_S = 120.0
SESSION_ANSWERS = json.loads(synthetic.ANSWERS.read_text(encoding="utf-8"))["session"]["answers"]


def synthetic_wavs() -> dict[str, bytes]:
    """
    합성 신호를 파일로 쓰지 않고 WAV 바이트로 만들어 모음
    입력: 없음 (fixtures/synthetic/signals.json)
    출력: 파일 이름 → WAV 바이트 사전 (모노, 16비트 PCM, signals.json의 샘플레이트)
    알고리즘
    1. signals.json을 읽어 합성기(Synth)를 만듦
    2. signals.json의 파일마다 신호를 만들고, −1~1로 자른 뒤 32767을 곱해 반올림한 16비트 정수로 바꿈
    3. 메모리 안에서 모노 16비트 WAV로 써서 바이트를 모음
    """
    synth = synthetic.Synth(synthetic.load_signals())
    out = {}
    for name in synth.s["files"]:
        buffer = io.BytesIO()
        pcm = np.round(np.clip(synth.build(name), -1, 1) * synthetic.FULL_SCALE).astype("<i2")
        with wave.open(buffer, "wb") as file:
            file.setnchannels(1)
            file.setsampwidth(2)
            file.setframerate(synth.rate)
            file.writeframes(pcm.tobytes())
        out[name] = buffer.getvalue()
    return out


def seconds_of(data: bytes) -> float:
    """
    WAV 바이트의 길이를 초로 셈
    입력: data (WAV 파일 바이트)
    출력: 길이(초, 소수 셋째 자리까지 반올림)
    알고리즘
    1. WAV 헤더의 프레임 수 ÷ 샘플레이트(Hz)를 반올림해 돌려줌
    """
    with wave.open(io.BytesIO(data), "rb") as file:
        return round(file.getnframes() / file.getframerate(), 3)


def make_wav(
    seconds: float,
    *,
    rate: int = RATE,
    channels: int = 1,
    tone_hz: float | None = 220.0,
    glide_to_hz: float | None = None,
    level: float = 0.3,
    noise: float = 0.0,
    seed: int = 0,
) -> bytes:
    """
    정현파에 잡음을 더한 16비트 PCM WAV를 만들어 냄. 같은 인자면 같은 파일임
    입력:
    - seconds: 길이(초)
    - rate: 샘플레이트(Hz, 기본 48000)
    - channels: 채널 수(기본 1. 2면 같은 표본을 두 채널에 넣음)
    - tone_hz: 정현파 주파수(Hz, 기본 220). None이면 정현파 없음
    - glide_to_hz: 끝 주파수(Hz). 주면 tone_hz에서 이 값까지 고르게 미끄러뜨림
    - level: 정현파 진폭(디지털 최대 크기 1 기준, 기본 0.3)
    - noise: 균등 잡음 진폭(최대 크기 1 기준, 0이면 잡음 없음)
    - seed: 잡음 난수 시드
    출력: WAV 파일 바이트(16비트 PCM)
    알고리즘
    1. 표본 수 = 길이(초) × 샘플레이트를 반올림해 0으로 채운 신호를 만듦
    2. tone_hz가 있으면 시작·끝 주파수 사이를 직선으로 잇는 주파수의 누적 위상으로 정현파를 더함
    3. noise가 0이 아니면 시드를 고정한 −noise~+noise 균등 잡음을 더함
    4. −1~1로 자르고 32767을 곱해 16비트 정수로 바꾼 뒤, 채널 수만큼 표본을 반복해 WAV로 씀
    """
    n = round(seconds * rate)
    signal = np.zeros(n)
    if tone_hz is not None:
        frequency = np.linspace(tone_hz, glide_to_hz or tone_hz, n)
        signal += level * np.sin(2 * np.pi * np.cumsum(frequency) / rate)
    if noise:
        signal += noise * np.random.default_rng(seed).uniform(-1, 1, n)
    samples = (np.clip(signal, -1, 1) * 32767).astype("<i2")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as file:
        file.setnchannels(channels)
        file.setsampwidth(2)
        file.setframerate(rate)
        file.writeframes(np.repeat(samples, channels).tobytes())
    return buffer.getvalue()


@dataclass(frozen=True)
class Reply:
    status: int
    body: bytes

    def json(self):
        """
        응답 본문을 JSON 값으로 바꿈
        입력: 없음
        출력: 풀어낸 JSON 값. 본문이 비어 있으면 None. JSON이 아니면 ValueError
        알고리즘
        1. 본문이 있으면 JSON으로 풀고, 없으면 None을 돌려줌
        """
        return json.loads(self.body) if self.body else None


class Http(Protocol):
    def send(self, method: str, url: str, headers: dict[str, str], body: bytes | None) -> Reply:
        """
        HTTP 요청 하나를 보내고 응답을 받는 함수의 모양을 정함
        입력: method (HTTP 메서드), url (요청 주소), headers (요청 헤더), body (요청 본문 바이트, 없으면 None)
        출력: 응답(Reply: 상태 코드, 본문 바이트)
        알고리즘
        1. 구현 없음. UrllibHttp나 테스트용 가짜가 구현하고, 4xx·5xx도 예외 대신 응답으로 돌려줘야 함
        """
        ...


class UrllibHttp:
    """표준 라이브러리만 쓰는 HTTP. 4xx·5xx도 예외 대신 응답으로 돌려준다."""

    def __init__(self, timeout: float = 30) -> None:
        """
        표준 라이브러리 HTTP 송신기의 제한 시간을 정함
        입력: timeout (요청 하나의 제한 시간, 초, 기본 30)
        출력: 없음
        알고리즘
        1. 제한 시간을 저장함
        """
        self.timeout = timeout

    def send(self, method: str, url: str, headers: dict[str, str], body: bytes | None) -> Reply:
        """
        표준 라이브러리(urllib)로 HTTP 요청을 보내고, 4xx·5xx도 예외 대신 응답으로 돌려줌
        입력: method (HTTP 메서드), url (요청 주소), headers (요청 헤더), body (요청 본문 바이트, 없으면 None)
        출력: 응답(Reply: 상태 코드, 본문 바이트). 연결 실패 같은 네트워크 오류는 예외로 그대로 나감
        알고리즘
        1. 요청을 만들어 제한 시간 안에 보내고 상태 코드와 본문을 읽음
        2. HTTPError(4xx·5xx)면 그 상태 코드와 본문을 응답으로 돌려줌
        """
        request = Request(url, data=body, method=method, headers=headers)
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return Reply(response.status, response.read())
        except HTTPError as error:
            return Reply(error.code, error.read())


class Api:
    def __init__(self, base_url: str, app_key: str, http: Http) -> None:
        """
        서버 API 호출기를 서버 주소·앱 키·HTTP 송신기로 준비함
        입력: base_url (서버 주소, 끝의 / 는 뗌), app_key (X-App-Key 헤더 값), http (HTTP 송신기)
        출력: 없음
        알고리즘
        1. 서버 주소 끝의 / 를 떼고 세 값을 저장함
        """
        self.base_url = base_url.rstrip("/")
        self.app_key = app_key
        self.http = http

    def call(self, method: str, path: str, body: dict | None = None, with_key: bool = True) -> Reply:
        """
        서버 API 요청 하나를 앱 키와 JSON 본문을 붙여 보냄
        입력: method (HTTP 메서드), path (서버 주소 뒤의 경로, 예: /v1/metrics), body (JSON으로 보낼 본문, 없으면 None), with_key (X-App-Key를 붙일지, 기본 참)
        출력: 응답(Reply)
        알고리즘
        1. Accept: application/json 헤더를 넣고, with_key면 X-App-Key를 붙임
        2. 본문이 있으면 JSON 바이트로 바꾸고 Content-Type: application/json을 붙임
        3. 서버 주소 + 경로로 보냄
        """
        headers = {"Accept": "application/json"}
        if with_key:
            headers["X-App-Key"] = self.app_key
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode()
        return self.http.send(method, self.base_url + path, headers, data)


def describe(reply: Reply) -> str:
    """
    실패를 보여줄 때 쓸 응답 설명을 만들어 냄. 본문에 URL이 있을 수 있어 오류 코드와 메시지만 꺼냄
    입력: reply (서버 응답)
    출력: "HTTP 상태" 또는 "HTTP 상태 code: message" 문자열
    알고리즘
    1. 본문 JSON의 error를 꺼내고, JSON이 아니거나 모양이 다르면 없는 것으로 봄
    2. 상태 코드에 error가 있으면 그 code와 message만 붙여 돌려줌
    """
    try:
        error = (reply.json() or {}).get("error")
    except (ValueError, AttributeError):
        error = None
    return f"HTTP {reply.status}" + (f" {error.get('code')}: {error.get('message')}" if error else "")


class StepFailed(Exception):
    """뒤 단계를 이어 갈 수 없는 실패"""


@dataclass
class Report:
    out: Callable[[str], None] = print
    passed: int = 0
    failed: list[str] = field(default_factory=list)

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        """
        확인 항목 하나의 통과·실패를 세고 출력함
        입력: name (확인 항목 이름), ok (통과 여부), detail (실패 때 덧붙일 설명, 선택)
        출력: ok를 그대로 돌려줌. 통과 수나 실패 목록을 늘리고 "통과  이름" 또는 "실패  이름: 설명"을 출력함
        알고리즘
        1. 통과면 통과 수를 1 늘리고 통과 줄을 출력함
        2. 실패면 실패 목록에 이름을 넣고 실패 줄(설명이 있으면 함께)을 출력함
        """
        if ok:
            self.passed += 1
            self.out(f"통과  {name}")
        else:
            self.failed.append(name)
            self.out(f"실패  {name}" + (f": {detail}" if detail else ""))
        return ok

    def require(self, name: str, ok: bool, detail: str = "") -> None:
        """
        뒤 단계를 이어 가는 데 꼭 필요한 항목을 확인하고, 실패하면 멈춤
        입력: name (확인 항목 이름), ok (통과 여부), detail (실패 때 덧붙일 설명, 선택)
        출력: 없음. 실패하면 StepFailed(name)를 일으킴
        알고리즘
        1. check로 세고 출력한 뒤, 실패면 StepFailed를 일으킴
        """
        if not self.check(name, ok, detail):
            raise StepFailed(name)


@dataclass
class Recording:
    kind: str
    data: bytes
    seconds: float
    fields: dict = field(default_factory=dict)
    recording_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def request(self, data: bytes | None = None, rate: int = RATE) -> dict:
        """
        업로드 URL 요청(upload-urls)에 넣을 녹음 항목 하나를 만들어 냄
        입력: data (크기를 잴 WAV 바이트, 없거나 비면 이 녹음의 파일), rate (알릴 샘플레이트, Hz, 기본 48000)
        출력: recording_id, kind, 종류별 필드, duration_s(초), sample_rate(Hz), size_bytes(바이트)를 담은 사전
        알고리즘
        1. 녹음 ID·종류·종류별 필드·길이와, 샘플레이트, 바이트 길이를 사전으로 묶어 돌려줌
        """
        data = data or self.data
        return {
            "recording_id": self.recording_id,
            "kind": self.kind,
            **self.fields,
            "duration_s": self.seconds,
            "sample_rate": rate,
            "size_bytes": len(data),
        }


class MockApp:
    """앱이 하는 순서대로 요청을 보내고, 응답이 API 명세대로인지 확인한다."""

    def __init__(
        self, api: Api, files: Http, ids_path: Path, report: Report, timeout_s: float = DEFAULT_TIMEOUT_S, poll_s: float = 1.0
    ) -> None:
        """
        흉내 앱이 쓸 연결·기록 위치와 새 사용자·세션 ID를 준비함
        입력:
        - api: 서버 API 호출기
        - files: S3 업로드·다운로드용 HTTP 송신기
        - ids_path: 만든 ID를 기록할 목록 파일 경로
        - report: 확인 결과를 세는 보고기
        - timeout_s: 세션마다 서버 처리를 기다리는 최대 시간(초, 기본 120)
        - poll_s: 처리 상태를 다시 묻는 간격(초, 기본 1)
        출력: 없음
        알고리즘
        1. 인자를 저장함
        2. 사용자 ID, 훈련 세션 ID, 자동 게인 조절 시험 세션 ID를 UUID v4로 새로 만듦
        3. 기록할 S3 키 목록과, 올린 파일(녹음 ID → 바이트) 사전을 비워 둠
        """
        self.api = api
        self.timeout_s = timeout_s
        self.poll_s = poll_s
        self.files = files
        self.ids_path = ids_path
        self.report = report
        self.user_id = str(uuid.uuid4())
        self.training_id = str(uuid.uuid4())
        self.agc_id = str(uuid.uuid4())
        self.s3_keys: list[str] = []
        self.uploaded: dict[str, bytes] = {}

    def save_ids(self) -> None:
        """
        정리 스크립트(tools.cleanup)가 읽는 ID 목록 파일을 씀. 요청을 보내기 전에 먼저 써 둠
        입력: 없음
        출력: 없음. ids_path에 {"user_ids", "session_ids"(훈련·자동 게인 조절 시험), "s3_keys"} JSON을 씀(폴더가 없으면 만듦)
        알고리즘
        1. 목록 파일의 폴더를 만들고, 지금까지의 ID와 S3 키를 JSON으로 덮어씀
        """
        self.ids_path.parent.mkdir(parents=True, exist_ok=True)
        content = {"user_ids": [self.user_id], "session_ids": [self.training_id, self.agc_id], "s3_keys": self.s3_keys}
        self.ids_path.write_text(json.dumps(content, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def expect(self, name: str, reply: Reply, status: int, check: Callable[[dict], bool] | None = None) -> dict:
        """
        응답의 상태 코드와 모양을 확인하고, 다르면 흐름을 멈춤
        입력: name (확인 항목 이름), reply (서버 응답), status (기대 HTTP 상태 코드), check (본문을 받아 참·거짓을 내는 추가 확인, 선택)
        출력: 응답 본문 JSON(본문이 없으면 빈 사전). 상태 코드가 다르거나 추가 확인이 거짓이면 StepFailed
        알고리즘
        1. 본문을 JSON으로 풀고, 상태 코드가 같고 추가 확인이 참인지 봄
        2. 응답 모양이 명세와 달라 확인하지 못한 경우(ValueError·KeyError·IndexError·TypeError)도 실패로 봄
        3. require로 결과를 세고, 실패면 기대 상태와 받은 응답 설명을 남기고 멈춤
        """
        try:
            body = reply.json() if reply.body else {}
            ok = reply.status == status and (check is None or bool(check(body)))
        except (ValueError, KeyError, IndexError, TypeError):
            # 응답 모양이 명세와 달라 확인하지 못한 경우도 실패다.
            body, ok = {}, False
        self.report.require(name, ok, f"기대 HTTP {status}, 받음 {describe(reply)}")
        return body

    def put_session(self, session_id: str, purpose: str, started_at: datetime) -> None:
        """
        세션을 등록하고 open 상태로 만들어졌는지 확인함
        입력: session_id (세션 ID), purpose (세션 목적: training·agc_test 등), started_at (세션 시작 시각)
        출력: 없음. 서버에 세션을 등록함. 200이 아니거나 status가 open이 아니거나 user_id가 다르면 StepFailed
        알고리즘
        1. 고정된 기기 정보(device_model mock_app, os_version 이 컴퓨터의 플랫폼, app_version mock-0.2.0, input_device synthetic, 샘플레이트 48000 Hz)와 client_meta로 세션 등록을 보냄
        2. 200이고 status가 open, user_id가 이 사용자인지 확인함
        """
        body = {
            "user_id": self.user_id,
            "purpose": purpose,
            "started_at": started_at.isoformat(),
            "device_model": "mock_app",
            "os_version": sys.platform,
            "app_version": "mock-0.2.0",
            "input_device": "synthetic",
            "sample_rate": RATE,
            "client_meta": {"tool": "mock_app"},
        }
        reply = self.api.call("PUT", f"/v1/sessions/{session_id}", body)
        self.expect(f"세션 등록({purpose})", reply, 200, lambda b: b["status"] == "open" and b["user_id"] == self.user_id)

    def upload_urls(self, session_id: str, recordings: list[dict]) -> dict[str, dict]:
        """
        녹음 목록의 업로드 URL을 받고, 생길 S3 키를 ID 목록 파일에 기록함
        입력: session_id (세션 ID), recordings (Recording.request로 만든 녹음 항목 목록)
        출력: 녹음 ID → 업로드 정보(url, method, headers, expires_at) 사전. 200이 아니거나 응답 순서가 요청과 다르면 StepFailed. ID 목록 파일을 다시 씀
        알고리즘
        1. 업로드 URL 요청을 보내고, 200이며 응답의 녹음 ID 순서가 요청과 같은지 확인함
        2. 녹음마다 S3 키 users/{user_id}/sessions/{session_id}/{recording_id}.wav를 겹치지 않게 기록함
        3. ID 목록 파일을 다시 쓰고, 녹음 ID로 찾을 수 있는 사전을 돌려줌
        """
        reply = self.api.call("POST", f"/v1/sessions/{session_id}/upload-urls", {"recordings": recordings})
        body = self.expect(
            f"업로드 URL {len(recordings)}개",
            reply,
            200,
            lambda b: [u["recording_id"] for u in b["uploads"]] == [r["recording_id"] for r in recordings],
        )
        uploads = {u["recording_id"]: u for u in body["uploads"]}
        for upload in uploads.values():
            key = f"users/{self.user_id}/sessions/{session_id}/{upload['recording_id']}.wav"
            if key not in self.s3_keys:
                self.s3_keys.append(key)
        self.save_ids()
        return uploads

    def put_file(self, upload: dict, data: bytes, name: str) -> None:
        """
        받은 업로드 URL로 파일 하나를 S3에 올림
        입력: upload (업로드 정보: method, url, headers), data (올릴 WAV 바이트), name (보고에 쓸 이름)
        출력: 없음. S3에 파일을 올리고, 올린 바이트를 녹음 ID로 기억함. 200이 아니면 StepFailed
        알고리즘
        1. 응답의 method·url로 보내고 headers를 그대로 붙임
        2. 200인지 확인하고, 올린 바이트를 기억함(뒤에서 열람 URL로 받은 파일과 비교함)
        """
        reply = self.files.send(upload["method"], upload["url"], dict(upload["headers"]), data)
        self.report.require(f"S3 올리기: {name}", reply.status == 200, f"HTTP {reply.status}")
        self.uploaded[upload["recording_id"]] = data

    def complete(self, session_id: str, body: dict) -> Reply:
        """
        세션 완료(complete) 요청을 보냄
        입력: session_id (세션 ID), body (complete 요청 본문)
        출력: 서버 응답(Reply). 확인은 하지 않음
        알고리즘
        1. POST /v1/sessions/{session_id}/complete를 보내고 응답을 그대로 돌려줌
        """
        return self.api.call("POST", f"/v1/sessions/{session_id}/complete", body)

    def wait_done(self, session_id: str, name: str) -> dict:
        """
        서버 지표 계산이 끝날(done 또는 failed) 때까지 기다림
        입력: session_id (세션 ID), name (보고에 쓸 세션 이름)
        출력: 마지막으로 받은 세션 상세 응답. 제한 시간(timeout_s, 초) 안에 done이 되지 않으면 StepFailed(상태, 오류, 기다린 초를 함께 보고)
        알고리즘
        1. 세션 상세를 묻고, 200이 아니면 빈 본문으로 봄
        2. status가 done이나 failed가 되면 멈추고, 아니면 poll_s초 쉬고 다시 물음. 제한 시간이 지나면 멈춤
        3. status가 done인지 require로 확인함
        """
        started = time.monotonic()
        body: dict = {}
        while time.monotonic() - started < self.timeout_s:
            reply = self.api.call("GET", f"/v1/sessions/{session_id}")
            body = reply.json() if reply.status == 200 else {}
            if body.get("status") in ("done", "failed"):
                break
            time.sleep(self.poll_s)
        waited = time.monotonic() - started
        detail = f"상태 {body.get('status')}, 오류 {body.get('error')}, {waited:.0f}초 기다림"
        self.report.require(f"서버 처리 완료(done): {name}", body.get("status") == "done", detail)
        return body

    def check_server_values(self, detail: dict) -> None:
        """
        서버 지표가 합성 신호 정답의 허용 범위 안인지 확인함
        입력: detail (세션 상세 응답, server_measurements 포함)
        출력: 없음. 지표마다 통과·실패를 보고함(실패해도 멈추지 않음)
        알고리즘
        1. 서버 지표를 지표 이름으로 찾을 수 있게 모음
        2. 정답 파일(answers.json의 session.answers)의 지표마다 확인함. calibration_으로 시작하는 정답은 건너뜀
        3. 값이 있고, |서버 값 − 정답| ≤ 허용 오차이고, 신뢰 낮음이 아니면 통과로 셈
        """
        values = {m["metric"]: m for m in detail["server_measurements"]}
        for metric, expected in SESSION_ANSWERS.items():
            if metric.startswith("calibration_"):
                continue
            got = values.get(metric)
            shown = "없음" if got is None else f"{got['value']:.3f}"
            ok = got is not None and abs(got["value"] - expected["value"]) <= expected["tolerance"] and not got["low_reliability"]
            self.report.check(f"서버 지표 {metric} = {shown} (정답 {expected['value']} ± {expected['tolerance']})", ok)

    def run(self) -> None:
        """
        앱이 하는 순서대로 사용자 등록부터 녹음 열람까지 서버에 실제로 보내고, 응답이 API 명세대로인지 확인함
        입력: 없음
        출력: 없음. 서버에 사용자·세션 2개(훈련, 자동 게인 조절 시험)·녹음을 만들고 S3에 파일을 올림. 꼭 필요한 단계가 실패하면 StepFailed
        알고리즘
        1. ID 목록 파일을 먼저 씀
        2. /health(앱 키 없이 200), 앱 키 없는 /v1/metrics(401 INVALID_APP_KEY), /v1/metrics(지표 11개)를 확인함
        3. 사용자를 등록하고, 같은 요청을 다시 보내 같은 응답이 오는지 확인함
        4. 시작 시각을 30분 전으로 한 훈련 세션을 등록함
        5. 합성 신호로 녹음 6개를 만듦: calibration_glide(f0_glide), noise_floor, mpt_trial 1(broken_phonation), mpt_trial 2(steady_phonation), eval_sentence s1(paused_speech), game(300 Hz 정현파 2초)
        6. 6개의 업로드 URL을 받고, mpt_trial 2만 빼고 올림
        7. 기기 지표 3개와 게임 라운드 기록 1개(breath_hold)를 담아 complete를 보내고, 409 MISSING_RECORDINGS에 mpt_trial 2만 있는지 확인함
        8. mpt_trial 2 자리에 스테레오 WAV를 올리고 complete를 보내, 422 INVALID_RECORDING_FORMAT에 그 녹음만 있는지 확인함
        9. 같은 녹음 ID로 업로드 URL을 다시 받아 맞는 파일을 올리고, complete가 202 processing인지, 다시 보내도 같은 202인지 확인함
        10. 서버 처리가 done이 될 때까지 기다리고, 기준선 회차 표시(is_baseline)와 서버 지표 정답 범위를 확인함
        11. 시작 시각을 40분 뒤로 한 자동 게인 조절 시험 세션을 등록하고, 220 Hz 1초 소리 3개(진폭 0.1·0.2·0.3, agc_steps·agc_hold·agc_toggle)를 agc_tone으로 올림
        12. complete가 202인지, done이 된 뒤 서버 지표가 비어 있는지 확인함(판정은 분석 스크립트가 따로 함)
        13. 진전도가 지표 8개 모두 이 훈련 회차를 기준선으로 하고, 회차 값 1개, 변화량 0, 기기 값은 이 회차의 3개인지 확인함
        14. 평가 문장 녹음의 열람 URL을 받아 내려받고, 올린 파일과 같은지 확인함
        """
        self.save_ids()
        api = self.api

        self.expect("상태 확인(/health)", api.call("GET", "/health", with_key=False), 200, lambda b: b == {"status": "ok"})
        self.expect("앱 키 없음은 401", api.call("GET", "/v1/metrics", with_key=False), 401, lambda b: b["error"]["code"] == "INVALID_APP_KEY")
        self.expect("지표 정의 11개", api.call("GET", "/v1/metrics"), 200, lambda b: len(b["metrics"]) == 11)

        created = {"created_at": datetime.now(UTC).isoformat()}
        first = self.expect("사용자 등록", api.call("PUT", f"/v1/users/{self.user_id}", created), 200)
        self.expect("사용자 등록 반복(같은 응답)", api.call("PUT", f"/v1/users/{self.user_id}", created), 200, lambda b: b == first)

        started = datetime.now(UTC) - timedelta(minutes=30)
        self.put_session(self.training_id, "training", started)
        run_id = str(uuid.uuid4())
        wavs = synthetic_wavs()

        def measured(kind: str, name: str, **fields) -> Recording:
            """
            합성 신호 파일 하나로 녹음 항목을 만들어 냄
            입력: kind (녹음 종류), name (합성 신호 파일 이름), fields (종류별 필드, 예: trial_no, sentence_id)
            출력: 녹음(Recording: 종류, WAV 바이트, 길이(초), 종류별 필드, 새 녹음 ID)
            알고리즘
            1. 만들어 둔 합성 WAV와 그 길이(초)로 Recording을 만듦
            """
            return Recording(kind, wavs[name], seconds_of(wavs[name]), fields)

        glide = measured("calibration_glide", "f0_glide")
        quiet = measured("noise_floor", "noise_floor")
        trial_1 = measured("mpt_trial", "broken_phonation", trial_no=1)
        trial_2 = measured("mpt_trial", "steady_phonation", trial_no=2)
        sentence = measured("eval_sentence", "paused_speech", sentence_id="s1")
        game = Recording("game", make_wav(2.0, tone_hz=300), 2.0, {"game_run_id": run_id})
        recordings = [glide, quiet, trial_1, trial_2, sentence, game]
        uploads = self.upload_urls(self.training_id, [r.request() for r in recordings])
        for recording in recordings:
            if recording is not trial_2:
                self.put_file(uploads[recording.recording_id], recording.data, recording.kind)

        complete_body = {
            "ended_at": datetime.now(UTC).isoformat(),
            "device_measurements": [
                {"metric": "max_phonation_time", "value": 3.0, "unit": "s"},
                {"metric": "f0_median", "value": 220.0, "unit": "Hz"},
                {"metric": "noise_floor", "value": -60.5, "unit": "dBFS"},
            ],
            "game_runs": [
                {
                    "game_run_id": run_id,
                    "game": "breath_hold",
                    "level": 1,
                    "round_index": 1,
                    "started_at": (started + timedelta(minutes=20)).isoformat(),
                    "params": {"target_s": 3},
                    "success_rate_pct": 80.0,
                    "decision": "up",
                    "result": {"best_s": 3.0},
                }
            ],
            "client_meta": {"tool": "mock_app"},
        }
        self.expect(
            "올리지 않은 녹음은 409",
            self.complete(self.training_id, complete_body),
            409,
            lambda b: b["error"]["missing_recording_ids"] == [trial_2.recording_id],
        )

        stereo = make_wav(trial_2.seconds, tone_hz=200, channels=2)
        self.put_file(uploads[trial_2.recording_id], stereo, "스테레오(틀린 파일)")
        self.expect(
            "스테레오 WAV는 422",
            self.complete(self.training_id, complete_body),
            422,
            lambda b: b["error"]["code"] == "INVALID_RECORDING_FORMAT"
            and [r["recording_id"] for r in b["error"]["invalid_recordings"]] == [trial_2.recording_id],
        )

        retry = self.upload_urls(self.training_id, [trial_2.request()])
        self.put_file(retry[trial_2.recording_id], trial_2.data, "고친 mpt_trial")
        accepted = {"session_id": self.training_id, "status": "processing"}
        self.expect("complete 202", self.complete(self.training_id, complete_body), 202, lambda b: b == accepted)
        self.expect("complete 반복(같은 응답)", self.complete(self.training_id, complete_body), 202, lambda b: b == accepted)
        detail = self.wait_done(self.training_id, "훈련 세션")
        self.report.check("기준선 회차 표시(is_baseline)", detail["is_baseline"] is True)
        self.check_server_values(detail)

        self.put_session(self.agc_id, "agc_test", started + timedelta(minutes=40))
        tones = [
            Recording("agc_tone", make_wav(1.0, tone_hz=220, level=0.1 * (i + 1)), 1.0, {"agc_signal": signal})
            for i, signal in enumerate(["agc_steps", "agc_hold", "agc_toggle"])
        ]
        agc_uploads = self.upload_urls(self.agc_id, [t.request() for t in tones])
        for tone in tones:
            self.put_file(agc_uploads[tone.recording_id], tone.data, tone.fields["agc_signal"])
        self.expect(
            "자동 게인 조절 시험 complete 202",
            self.complete(self.agc_id, {"ended_at": datetime.now(UTC).isoformat()}),
            202,
        )
        agc = self.wait_done(self.agc_id, "자동 게인 조절 시험 세션")
        self.report.check("자동 게인 조절 시험 세션은 서버 지표 없음(분석 스크립트가 따로 판정)", agc["server_measurements"] == [])

        # 첫 훈련 회차라 모든 진전도 지표의 기준선이 이 세션이고 변화량은 0이다.
        self.expect(
            "진전도(지표 8개 모두 이 회차가 기준선, 변화량 0, 기기 값 1회차)",
            api.call("GET", f"/v1/users/{self.user_id}/history"),
            200,
            lambda b: len(b["metrics"]) == 8
            and all(
                m["baseline"]["session_id"] == self.training_id and len(m["series"]) == 1 and m["latest"]["change"] == 0
                for m in b["metrics"]
            )
            and [s["session_id"] for s in b["recent_device_measurements"]] == [self.training_id]
            and len(b["recent_device_measurements"][0]["measurements"]) == 3,
        )

        link = self.expect("녹음 열람 URL", api.call("GET", f"/v1/recordings/{sentence.recording_id}/url"), 200)
        download = self.files.send("GET", link["url"], {}, None)
        self.report.check(
            "열람 URL로 받은 파일이 올린 파일과 같음",
            download.status == 200 and download.body == self.uploaded[sentence.recording_id],
            f"HTTP {download.status}",
        )


def clean_up(ids_path: Path, db=None, storage=None, out: Callable[[str], None] = print) -> bool:
    """
    정리 스크립트(tools.cleanup)로 만든 데이터를 지우고, DB와 S3에 남은 것이 없는지 확인함
    입력: ids_path (ID 목록 파일 경로), db (DB 세션, 없으면 환경 변수·backend/.env의 DATABASE_URL로 연결), storage (녹음 버킷 저장소, 없으면 환경 변수·backend/.env의 AWS_*·S3_BUCKET으로 만듦), out (결과를 출력할 함수, 기본 print)
    출력: 깨끗하면 참(정리 종료 코드 0, 남은 S3 객체·DB 행 없음). S3 객체와 DB 행을 지우고, 남은 DB 행·S3 객체 수를 출력함
    알고리즘
    1. db·storage가 없으면 운영 접속 정보로 만듦
    2. tools.cleanup.main을 목록 파일과 --yes로 실행함
    3. 목록 파일의 사용자 저장 위치(users/{user_id}/) 아래 남은 S3 키와, 남은 사용자·세션 행을 찾음
    4. 결과를 한 줄로 출력하고, 종료 코드가 0이고 남은 것이 없는지 돌려줌
    """
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from app.db.models import SessionRow, UserRow
    from app.storage.s3 import user_prefix
    from tools import cleanup

    if db is None or storage is None:
        from app.db.engine import get_engine
        from app.storage.s3 import get_storage

        db = db or Session(get_engine())
        storage = storage or get_storage()
    code = cleanup.main([str(ids_path), "--yes"], db=db, storage=storage)
    ids = json.loads(ids_path.read_text(encoding="utf-8"))
    user_ids = [uuid.UUID(value) for value in ids["user_ids"]]
    session_ids = [uuid.UUID(value) for value in ids["session_ids"]]
    left_keys = [key for user_id in user_ids for key in storage.keys_under(user_prefix(user_id))]
    left_rows = db.scalars(select(UserRow.id).where(UserRow.id.in_(user_ids))).all() + db.scalars(
        select(SessionRow.id).where(SessionRow.id.in_(session_ids))
    ).all()
    clean = code == 0 and not left_keys and not left_rows
    out(f"{'통과' if clean else '실패'}  정리 후 남은 것: DB 행 {len(left_rows)}개, S3 객체 {len(left_keys)}개")
    return clean


def main(
    argv: list[str] | None = None,
    *,
    api: Api | None = None,
    files: Http | None = None,
    cleaner: Callable[[Path], bool] | None = None,
    ids_dir: Path = IDS_DIR,
    out: Callable[[str], None] = print,
    poll_s: float = 1.0,
) -> int:
    """
    합성 WAV로 앱의 전체 흐름을 서버에 보내 보는 명령을 실행함
    입력:
    - argv: 명령줄 인자 목록(없으면 sys.argv). --base-url(서버 주소, 기본 http://127.0.0.1:8000), --keep(만든 데이터를 지우지 않음), --timeout(세션마다 서버 처리를 기다리는 최대 시간, 초, 기본 120)
    - api: 서버 API 호출기(시험용 주입, 없으면 환경 변수·backend/.env의 APP_API_KEY로 만듦)
    - files: S3용 HTTP 송신기(시험용 주입, 없으면 urllib)
    - cleaner: 정리 함수(시험용 주입, 없으면 clean_up)
    - ids_dir: ID 목록 파일을 둘 폴더(기본 저장소 루트의 data/mock_app/)
    - out: 결과를 출력할 함수(기본 print)
    - poll_s: 처리 상태를 다시 묻는 간격(초, 기본 1)
    출력: 종료 코드(0 = 모든 확인 통과이고 정리 성공, 1 = 실패나 정리 실패, 2 = APP_API_KEY 없음). ids_dir에 ID 목록 파일을 씀. --keep이 아니면 끝난 뒤 만든 데이터를 지움
    알고리즘
    1. 명령줄 인자를 읽음
    2. api가 없으면 APP_API_KEY로 만들고, 키가 없으면 표준 오류에 알리고 2를 돌려줌
    3. ID 목록 파일 이름을 지금 UTC 시각(YYYYmmdd-HHMMSS)과 무작위 16진수 6자리로 정함
    4. 서버 주소와 ID 기록 위치를 출력하고 MockApp.run을 실행함. StepFailed면 나머지를 건너뛴다고 출력함
    5. 예외가 나도 마지막에 정리함: --keep이면 나중에 지우는 명령을 출력하고, 아니면 정리 함수를 실행함. 예상 밖 예외는 정리 뒤 그대로 나감
    6. 통과·실패 수(정리 실패면 함께)를 출력하고 종료 코드를 돌려줌
    """
    parser = argparse.ArgumentParser(description="합성 WAV로 앱의 전체 흐름을 서버에 보내 본다")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help=f"서버 주소(기본 {DEFAULT_BASE_URL})")
    parser.add_argument("--keep", action="store_true", help="끝난 뒤 만든 데이터를 지우지 않는다")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S, help="세션마다 서버 처리를 기다리는 최대 시간(초)")
    args = parser.parse_args(argv)

    if api is None:
        from app.settings import get_settings

        key = get_settings().app_api_key
        if key is None:
            print("APP_API_KEY가 없습니다. backend/.env나 환경 변수에 넣으세요", file=sys.stderr)
            return 2
        api = Api(args.base_url, key.get_secret_value(), UrllibHttp())
    files = files or UrllibHttp()
    ids_path = ids_dir / f"{datetime.now(UTC):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}.json"

    report = Report(out)
    out(f"서버: {api.base_url or '(테스트 클라이언트)'}")
    out(f"ID 기록: {ids_path}")
    try:
        MockApp(api, files, ids_path, report, args.timeout, poll_s).run()
    except StepFailed:
        out("앞 단계가 실패해 나머지는 건너뜁니다")
    finally:
        clean = True
        if args.keep:
            out(f"--keep: 지우지 않았습니다. 나중에 python -m tools.cleanup {ids_path} --yes")
        else:
            clean = (cleaner or (lambda path: clean_up(path, out=out)))(ids_path)
    out(f"결과: 통과 {report.passed}개, 실패 {len(report.failed)}개" + ("" if clean else ", 정리 실패"))
    return 0 if not report.failed and clean else 1


if __name__ == "__main__":
    sys.exit(main())
