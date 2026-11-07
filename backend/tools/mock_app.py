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
    """합성 신호를 WAV 바이트로 만든다(파일로 쓰지 않음)."""
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
    """16비트 PCM WAV. 정현파(주파수를 미끄러뜨릴 수 있음)에 잡음을 더한다. 같은 인자면 같은 파일이다."""
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
        return json.loads(self.body) if self.body else None


class Http(Protocol):
    def send(self, method: str, url: str, headers: dict[str, str], body: bytes | None) -> Reply: ...


class UrllibHttp:
    """표준 라이브러리만 쓰는 HTTP. 4xx·5xx도 예외 대신 응답으로 돌려준다."""

    def __init__(self, timeout: float = 30) -> None:
        self.timeout = timeout

    def send(self, method: str, url: str, headers: dict[str, str], body: bytes | None) -> Reply:
        request = Request(url, data=body, method=method, headers=headers)
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return Reply(response.status, response.read())
        except HTTPError as error:
            return Reply(error.code, error.read())


class Api:
    def __init__(self, base_url: str, app_key: str, http: Http) -> None:
        self.base_url = base_url.rstrip("/")
        self.app_key = app_key
        self.http = http

    def call(self, method: str, path: str, body: dict | None = None, with_key: bool = True) -> Reply:
        headers = {"Accept": "application/json"}
        if with_key:
            headers["X-App-Key"] = self.app_key
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode()
        return self.http.send(method, self.base_url + path, headers, data)


def describe(reply: Reply) -> str:
    """실패를 보여줄 때 쓴다. 응답 본문에 URL이 있을 수 있어 오류 코드와 메시지만 꺼낸다."""
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
        if ok:
            self.passed += 1
            self.out(f"통과  {name}")
        else:
            self.failed.append(name)
            self.out(f"실패  {name}" + (f": {detail}" if detail else ""))
        return ok

    def require(self, name: str, ok: bool, detail: str = "") -> None:
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
        """정리 스크립트가 읽는 목록. 요청을 보내기 전에 먼저 써 둔다."""
        self.ids_path.parent.mkdir(parents=True, exist_ok=True)
        content = {"user_ids": [self.user_id], "session_ids": [self.training_id, self.agc_id], "s3_keys": self.s3_keys}
        self.ids_path.write_text(json.dumps(content, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def expect(self, name: str, reply: Reply, status: int, check: Callable[[dict], bool] | None = None) -> dict:
        try:
            body = reply.json() if reply.body else {}
            ok = reply.status == status and (check is None or bool(check(body)))
        except (ValueError, KeyError, IndexError, TypeError):
            # 응답 모양이 명세와 달라 확인하지 못한 경우도 실패다.
            body, ok = {}, False
        self.report.require(name, ok, f"기대 HTTP {status}, 받음 {describe(reply)}")
        return body

    def put_session(self, session_id: str, purpose: str, started_at: datetime) -> None:
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
        reply = self.files.send(upload["method"], upload["url"], dict(upload["headers"]), data)
        self.report.require(f"S3 올리기: {name}", reply.status == 200, f"HTTP {reply.status}")
        self.uploaded[upload["recording_id"]] = data

    def complete(self, session_id: str, body: dict) -> Reply:
        return self.api.call("POST", f"/v1/sessions/{session_id}/complete", body)

    def wait_done(self, session_id: str, name: str) -> dict:
        """서버 지표 계산이 끝날(done 또는 failed) 때까지 기다린다."""
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
        """서버 지표가 합성 신호 정답의 허용 범위 안인지 본다."""
        values = {m["metric"]: m for m in detail["server_measurements"]}
        for metric, expected in SESSION_ANSWERS.items():
            if metric.startswith("calibration_"):
                continue
            got = values.get(metric)
            shown = "없음" if got is None else f"{got['value']:.3f}"
            ok = got is not None and abs(got["value"] - expected["value"]) <= expected["tolerance"] and not got["low_reliability"]
            self.report.check(f"서버 지표 {metric} = {shown} (정답 {expected['value']} ± {expected['tolerance']})", ok)

    def run(self) -> None:
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
    """정리 스크립트로 지우고, DB와 S3에 남은 것이 없는지 확인한다."""
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
