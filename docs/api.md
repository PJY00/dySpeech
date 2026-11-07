# dySpeech API 명세

- 버전: **0.2.0** (서버의 `/openapi.json`에서 `info.version`)
- 대상: 앱 개발자. 이 문서와 [`openapi.json`](openapi.json)이 서버와 앱이 맞춰야 할 기준이다.
- 바뀐 내용은 [`api-changelog.md`](api-changelog.md)에 적는다. 지표의 정의와 계산 방법은 [`metrics-spec.md`](metrics-spec.md)에 있다.

---

## 1. 한눈에 보기

앱은 세션이 끝난 뒤에만 서버로 보낸다. 실시간 통신은 없다.

| 순서 | 요청 | 하는 일 |
|---|---|---|
| 1 | `PUT /v1/users/{user_id}` | 사용자 등록(처음 한 번) |
| 2 | `PUT /v1/sessions/{session_id}` | 세션 등록 |
| 3 | `POST /v1/sessions/{session_id}/upload-urls` | 녹음 파일마다 업로드 URL을 받는다 |
| 4 | (S3로 직접) `PUT {url}` | 받은 URL로 WAV를 올린다 |
| 5 | `POST /v1/sessions/{session_id}/complete` | 기기 지표, 게임 기록, 업로드 완료를 알린다 |
| 6 | `GET /v1/sessions/{session_id}` | 서버 처리 상태와 서버 지표를 본다 |
| 7 | `GET /v1/users/{user_id}/history` | 진전도와 변화 판정을 받는다 |

그 밖의 요청

| 요청 | 하는 일 |
|---|---|
| `GET /v1/metrics` | 지표 정의(단위, 바람직한 방향, 최소 측정 가능 변화량) |
| `GET /v1/recordings/{recording_id}/url` | 녹음을 들어 볼 수 있는 URL(10분 유효) |
| `GET /health` | 서버 상태 확인. 앱 키가 필요 없다 |

- 기준 주소: 서버 주소는 따로 알려준다. 로컬 서버는 `http://127.0.0.1:8000`이다.
- 요청을 직접 보내 볼 수 있는 문서 화면: `{기준 주소}/docs`(앱 키 없이 열린다. 오른쪽 위 Authorize에 키를 넣으면 `/v1` 요청도 보낼 수 있다)

### 1.1 지금 서버에서 아직 되지 않는 것

0.2.0부터 서버는 요청을 실제로 저장하고, 업로드 URL로 S3에 실제로 올라간다. 404·409·413·422는 이 문서대로 돌아온다. 0.1.0의 임시 서버(예시 응답만 돌려줌)는 0.2.0 배포로 끝난다.

서버 지표(공식 값) 계산은 아직 없다. 응답 형식은 그대로이고 값만 비어 있다.

- complete 뒤 세션 `status`는 `processing`에 머문다. `done`·`failed`로 바뀌는 것은 서버 지표 계산이 들어간 뒤다.
- `GET /v1/sessions/{session_id}`의 `server_measurements`는 빈 목록이다.
- 진전도의 지표 8개는 모두 `no_data`다. `recent_device_measurements`에는 앱이 보낸 기기 값이 실제로 들어간다.
- `GET /v1/metrics`의 최소 측정 가능 변화량은 아직 정해지지 않아 `null`이다.

---

## 2. 공통 규칙

### 2.1 형식과 인증

- 요청과 응답은 JSON, UTF-8이다.
- 모든 `/v1` 요청에 `X-App-Key` 헤더를 넣는다. 없거나 맞지 않으면 401이다. 키 값은 저장소 밖으로 따로 전달한다.
- **보안 수준**: 과제 시연용이다.
  - 앱 키는 앱 파일에서 꺼낼 수 있으므로 진짜 비밀이 아니다.
  - 사용자 데이터 보호는 "UUID를 모르면 접근할 수 없다" 수준이다. 로그인은 없다.

### 2.2 ID

- 사용자, 세션, 녹음, 게임 판 ID는 **앱이 UUID v4로 만든다.** 다른 버전의 UUID나 UUID가 아닌 값은 422다.
- 같은 요청을 다시 보내도 결과는 한 번 보낸 것과 같다(2.6).

### 2.3 시각

- ISO 8601 형식이고, **시간대가 있어야 한다.** 예: `2026-11-20T09:15:00Z`, `2026-11-20T18:15:00+09:00`
- 시간대가 없는 시각(`2026-11-20T09:15:00`)은 422다.
- 응답의 시각은 항상 UTC(`Z`)다.

### 2.4 단위

단위는 필드 이름(`duration_s`, `success_rate_pct`)이나 응답의 `unit`으로 밝힌다.

| 표기 | 뜻 |
|---|---|
| `s` | 초 |
| `dB/s` | 초당 데시벨 변화 |
| `dB` | 데시벨 차이(상대값) |
| `dBFS` | 디지털 최대 크기를 0으로 둔 상대 음량. 실제 소리 크기(dB SPL)가 아니다 |
| `Hz` | 헤르츠 |
| `semitones` | 반음 |
| `syllables/s` | 초당 음절 수 |
| `%` | 백분율(0~100) |

### 2.5 API 명세에 없는 필드

- API 명세에 없는 필드를 보내면 422다. 필드 이름 오타를 바로 찾기 위해서다.
- 명세를 바꾸지 않고 부가 정보를 보내려면 세션 등록과 complete 요청의 `client_meta`(자유 JSON 객체, 선택)에 넣는다. 서버는 저장만 하고 해석하지 않는다.
- `client_meta`, 게임 기록의 `params`·`result`를 포함한 요청 본문 전체는 1 MB를 넘을 수 없다(3.5).

### 2.6 같은 요청을 다시 보낼 때

- 같은 ID에 같은 내용으로 다시 보내면 처음과 같은 결과(200)를 돌려준다. 처음 등록이든 반복이든 상태 코드는 200이다.
- 같은 ID에 다른 내용을 보내면 409(`ID_CONFLICT`)다.
  - 세션 등록의 `client_meta`는 비교하지 않는다. 처음 받은 값을 둔다.
  - 녹음의 파일 정보(`duration_s`, `sample_rate`, `size_bytes`)는 complete 전에는 바꿔 보내도 된다(4.3).
- 세션 등록을 다시 보내면 지금 상태(`status`)가 담긴 세션을 돌려준다.
- complete를 여러 번 보내도 중복 저장하지 않는다.
- 그래서 네트워크가 끊기면 처음부터 다시 보내도 된다.

### 2.7 목록 순서

- 진전도의 `series`와 `recent_device_measurements`는 오래된 회차부터 나온다.
- 진전도의 `metrics`는 항상 같은 8개 지표가 같은 순서로 나온다(8장).

---

## 3. 오류

모든 오류는 같은 형식이다.

```json
{"error": {"code": "SESSION_NOT_FOUND", "message": "사람이 읽는 설명"}}
```

앱은 `code`로 처리를 나누고, `message`는 기록이나 개발 중 확인용으로만 쓴다.

| 상태 | `code` | 뜻 | 앱 처리 |
|---|---|---|---|
| 401 | `INVALID_APP_KEY` | `X-App-Key`가 없거나 맞지 않음 | 키 설정을 확인한다. 다시 보내도 같다 |
| 404 | `USER_NOT_FOUND` | 사용자 없음(세션 등록, 진전도) | 사용자 등록부터 다시 한다 |
| 404 | `SESSION_NOT_FOUND` | 세션 없음 | 세션 등록부터 다시 한다 |
| 404 | `RECORDING_NOT_FOUND` | 녹음 없음, 또는 보관 기한이 지남 | 열람할 수 없다 |
| 404 | `NOT_FOUND` | 없는 경로 | 주소를 확인한다 |
| 405 | `METHOD_NOT_ALLOWED` | 그 경로에 없는 메서드 | 메서드를 확인한다 |
| 409 | `ID_CONFLICT` | 같은 ID에 다른 내용이 이미 있음 | 새 ID를 만들거나 처음 보낸 내용을 다시 보낸다 |
| 409 | `MISSING_RECORDINGS` | complete 때 올라오지 않은 녹음이 있음 | `missing_recording_ids`의 파일만 다시 올리고 complete를 다시 보낸다 |
| 413 | `PAYLOAD_TOO_LARGE` | 요청 본문이 1 MB를 넘음 | 3.5 |
| 422 | `VALIDATION_ERROR` | 요청이 API 명세와 다름 | `fields`를 보고 앱을 고친다. 그대로 다시 보내면 안 된다 |
| 422 | `INVALID_RECORDING_FORMAT` | 올린 WAV가 규격과 다름 | 3.4 |
| 500 | `INTERNAL_ERROR` | 서버 문제 | 잠시 뒤 다시 보낸다 |

### 3.1 401 예시

<!-- example: ErrorResponse -->
```json
{"error": {"code": "INVALID_APP_KEY", "message": "X-App-Key가 없거나 맞지 않습니다"}}
```

### 3.2 422 예시

문제 필드마다 경로(`path`)와 이유(`reason`)가 `fields`에 들어 있다.

- 경로는 `body`(요청 본문)나 `path`(주소 안의 ID)로 시작한다. 목록 안의 항목은 `[번호]`(0부터)로 적는다.
- 아래는 첫째 녹음의 샘플레이트가 틀리고, 둘째 녹음(`mpt_trial`)에 `trial_no`가 빠진 경우다.

<!-- example: ValidationErrorResponse -->
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "요청이 API 명세와 다릅니다",
    "fields": [
      {"path": "body.recordings[0].sample_rate", "reason": "Input should be 16000, 44100 or 48000"},
      {"path": "body.recordings[1].trial_no", "reason": "mpt_trial 녹음에는 trial_no 값이 필요합니다"}
    ]
  }
}
```

- 녹음 종류별 필드 규칙(5.2)은 그 항목의 필드 형식이 모두 맞은 뒤에 검사한다. 그래서 한 항목에 형식 오류와 종류별 규칙 오류가 함께 있으면, 형식 오류를 고친 다음 요청에서 종류별 규칙 오류가 나온다.
- 같은 ID가 목록 안에서 겹치면 경로는 목록(`body.recordings`)을 가리키고, 이유에 겹친 값이 나온다.
- 본문이 JSON이 아니면 경로는 `body`이고, 이유는 "JSON 형식이 아닙니다(위치 …)"로 시작한다.

### 3.3 409 예시(올라오지 않은 녹음)

<!-- example: MissingRecordingsResponse -->
```json
{
  "error": {
    "code": "MISSING_RECORDINGS",
    "message": "올라오지 않은 녹음이 있습니다",
    "missing_recording_ids": ["3b9a7c15-4e6d-4c8b-a03f-8d1e5b4c9a27"]
  }
}
```

### 3.4 WAV 규격 422

complete 때 올라온 WAV의 헤더를 읽어 규격(5.1)을 검사한다. 아래 중 하나라도 어긋나면 아래처럼 422를 돌려준다.

- 모노, 16비트 PCM, 허용 샘플레이트
- 업로드 URL을 받을 때 알린 `sample_rate`와 파일의 샘플레이트가 같은지

<!-- example: InvalidRecordingsResponse -->
```json
{
  "error": {
    "code": "INVALID_RECORDING_FORMAT",
    "message": "녹음 파일이 규격과 다릅니다",
    "invalid_recordings": [
      {"recording_id": "2a8f6b04-3d5c-4b7a-9f2e-7c0d4a3b8f16", "reason": "채널이 2개입니다. 모노(1개)여야 합니다"},
      {"recording_id": "4cab8d26-5f7e-4d9c-b14a-9e2f6c5dab38", "reason": "샘플레이트 22050 Hz는 받지 않습니다. 16000, 44100, 48000 중 하나여야 합니다"}
    ]
  }
}
```

- **검사 순서**: 올라오지 않은 녹음 검사(409)를 먼저 하고, 모두 올라왔을 때 규격을 검사한다.
- **앱 처리**: `invalid_recordings`의 녹음만 규격에 맞게 다시 만든다. 같은 `recording_id`로 업로드 URL을 다시 받아 올린 뒤 complete를 다시 보낸다. 파일 정보(`sample_rate` 등)가 바뀌었으면 바뀐 값으로 요청한다(4.3).
- **같은 상태 코드의 다른 응답**: complete의 422는 이 응답과 `VALIDATION_ERROR`(3.2) 두 가지다. `code`로 구분한다. `openapi.json`에는 `InvalidRecordingsResponse`로 들어 있다.
- `reason`은 사람이 읽는 설명이다. 앱은 `reason`의 문장으로 처리를 나누지 않는다.

### 3.5 요청 크기 상한 413

모든 요청의 본문은 1 MB(1,048,576바이트)까지 받는다. 넘으면 본문을 읽지 않고 413을 돌려준다. 앱 키 확인보다 먼저 검사한다.

<!-- example: ErrorResponse -->
```json
{"error": {"code": "PAYLOAD_TOO_LARGE", "message": "요청 본문이 1 MB를 넘습니다"}}
```

- **대상**: JSON 요청 본문이다. WAV는 서버를 거치지 않고 S3로 직접 올리므로 이 상한과 상관없다.
- **보통 요청 크기**: 몇 KB이므로, 넘는다면 `client_meta`나 게임 기록의 `params`·`result`에 큰 값이 들어간 경우다.
- **앱 처리**: 그 값을 줄여서 다시 보낸다. 그대로 다시 보내면 같은 결과다.

---

## 4. 엔드포인트

경로의 `{user_id}`, `{session_id}`, `{recording_id}`는 UUID v4다. 아래 예시는 형식을 보여주기 위한 값이다(0.1.0 임시 서버의 응답). 실제 업로드·열람 URL은 S3 주소(`https://{버킷}.s3.ap-northeast-2.amazonaws.com/…`)이고 서명 때문에 길다. URL은 받은 그대로 쓰고, 고치거나 조립하지 않는다.

### 4.1 사용자 등록 `PUT /v1/users/{user_id}`

처음 한 번 보낸다. 같은 내용으로 다시 보내도 된다.

| 필드 | 형식 | 설명 |
|---|---|---|
| `created_at` | 시각 | 앱에서 사용자를 만든 시각 |

<!-- example: UserIn -->
```json
{"created_at": "2026-11-20T18:10:00+09:00"}
```

응답 200

<!-- example: User -->
```json
{"id": "3f9b2c1e-7a4d-4e8b-9c2f-1a6d5e8b7c40", "created_at": "2026-11-20T09:10:00Z"}
```

오류: 409 `ID_CONFLICT`, 422

### 4.2 세션 등록 `PUT /v1/sessions/{session_id}`

| 필드 | 형식 | 설명 |
|---|---|---|
| `user_id` | UUID v4 | 등록된 사용자 |
| `purpose` | `training` / `retest` / `agc_test` | 훈련 / 반복 측정(9.4) / 자동 게인 조절 시험(9.3) |
| `started_at` | 시각 | 세션 시작 시각 |
| `device_model`, `os_version`, `app_version` | 문자열(1~100자) | 기기 모델, 운영체제 버전, 앱 버전 |
| `input_device` | 문자열(1~100자) | 입력 장치. 예: `wired_headset` |
| `sample_rate` | `16000` / `44100` / `48000` | 녹음 샘플레이트 |
| `client_meta` | JSON 객체(선택) | 부가 정보. 서버는 저장만 한다(2.5) |

<!-- example: SessionIn -->
```json
{
  "user_id": "3f9b2c1e-7a4d-4e8b-9c2f-1a6d5e8b7c40",
  "purpose": "training",
  "started_at": "2026-11-20T18:15:00+09:00",
  "device_model": "SM-S921N",
  "os_version": "Android 16",
  "app_version": "0.1.0",
  "input_device": "wired_headset",
  "sample_rate": 48000,
  "client_meta": {"app_build": "dev-17"}
}
```

응답 200. `client_meta`는 응답에 넣지 않는다.

<!-- example: Session -->
```json
{
  "id": "8c41e7d2-5b3a-4f19-a6e2-9d0c7b1f3a58",
  "user_id": "3f9b2c1e-7a4d-4e8b-9c2f-1a6d5e8b7c40",
  "purpose": "training",
  "started_at": "2026-11-20T09:15:00Z",
  "ended_at": null,
  "device_model": "SM-S921N",
  "os_version": "Android 16",
  "app_version": "0.1.0",
  "input_device": "wired_headset",
  "sample_rate": 48000,
  "status": "open",
  "is_baseline": false
}
```

- `status`: `open`(등록됨) → `processing`(complete 뒤 서버 계산 중) → `done` 또는 `failed`
- `is_baseline`: 이 세션이 기준선 회차인지(8.1)

오류: 404 `USER_NOT_FOUND`, 409 `ID_CONFLICT`, 422

### 4.3 업로드 URL 발급 `POST /v1/sessions/{session_id}/upload-urls`

녹음 파일마다 한 항목을 보낸다. 녹음 규격과 종류는 5장에 있다.

| 필드 | 형식 | 설명 |
|---|---|---|
| `recording_id` | UUID v4 | 앱이 만든 녹음 ID |
| `kind` | 5.2의 종류 | 녹음 종류 |
| `trial_no` | 정수(1부터) | `mpt_trial`에만 넣는다 |
| `sentence_id` | `s1` | `eval_sentence`에만 넣는다. 서버 설정에 있는 평가 전용 문장 ID만 받는다 |
| `game_run_id` | UUID v4 | `game`에만 넣는다. complete의 `game_runs`에 같은 ID가 있어야 한다 |
| `agc_signal` | `agc_steps` / `agc_hold` / `agc_toggle` | `agc_tone`에만 넣는다. 재생한 시험 소리(9.3) |
| `duration_s` | 숫자(0 초과, 120 이하) | 녹음 길이(초) |
| `sample_rate` | `16000` / `44100` / `48000` | 샘플레이트 |
| `size_bytes` | 정수(1 이상) | 파일 크기(바이트) |

<!-- example: UploadUrlsIn -->
```json
{
  "recordings": [
    {"recording_id": "1d7e5a93-2c4b-4a6f-8e1d-6b9c3f2a7e05", "kind": "noise_floor", "duration_s": 2.1, "sample_rate": 48000, "size_bytes": 201644},
    {"recording_id": "2a8f6b04-3d5c-4b7a-9f2e-7c0d4a3b8f16", "kind": "mpt_trial", "trial_no": 1, "duration_s": 14.2, "sample_rate": 48000, "size_bytes": 1363244},
    {"recording_id": "3b9a7c15-4e6d-4c8b-a03f-8d1e5b4c9a27", "kind": "mpt_trial", "trial_no": 2, "duration_s": 15.0, "sample_rate": 48000, "size_bytes": 1440044},
    {"recording_id": "4cab8d26-5f7e-4d9c-b14a-9e2f6c5dab38", "kind": "eval_sentence", "sentence_id": "s1", "duration_s": 6.4, "sample_rate": 48000, "size_bytes": 614444},
    {
      "recording_id": "5dbc9e37-6a8f-4eab-825b-af3a7d6ebc49",
      "kind": "game",
      "game_run_id": "6ecdaf48-7b9a-4fbc-936c-ba4b8e7fcd5a",
      "duration_s": 32.0,
      "sample_rate": 48000,
      "size_bytes": 3072044
    }
  ]
}
```

평가 전용 문장의 `sentence_id`는 `s1`이다([`metrics-spec.md`](metrics-spec.md) 6장).

- 서버는 문장마다 정답 음절 수를 알아야 말속도 지표를 계산할 수 있다. 그래서 서버 설정에 없는 문장 ID는 422로 거부한다. 경로는 `body.recordings[번호].sentence_id`다.
- 문장이 늘면 `api-changelog.md`에 적는다.

응답 200. 요청 순서대로 한 항목씩 나온다.

<!-- example: UploadUrlsOut -->
```json
{
  "uploads": [
    {
      "recording_id": "1d7e5a93-2c4b-4a6f-8e1d-6b9c3f2a7e05",
      "url": "https://s3.example.com/example/8c41e7d2-5b3a-4f19-a6e2-9d0c7b1f3a58/1d7e5a93-2c4b-4a6f-8e1d-6b9c3f2a7e05.wav?X-Amz-Signature=example",
      "method": "PUT",
      "headers": {"Content-Type": "audio/wav"},
      "expires_at": "2026-11-20T09:56:00Z"
    }
  ]
}
```

(위 응답은 첫 항목만 보였다. 실제로는 요청한 녹음 수만큼 나온다.)

- 각 파일을 응답의 `method`, `url`로 보내고, `headers`를 **그대로** 붙인다. 헤더가 서명에 들어갈 수 있어서, 빼거나 바꾸면 업로드가 거부될 수 있다.
- URL은 15분 동안 유효하다(`expires_at`). 지나면 업로드 URL을 다시 요청한다. 같은 `recording_id`면 같은 저장 위치에 대한 새 URL이 나온다.
- 같은 `recording_id`로 다시 요청할 때
  - `kind`와 종류별 필드(`trial_no`, `sentence_id`, `game_run_id`, `agc_signal`)는 처음과 같아야 한다. 다르면 409다.
  - 파일 정보(`duration_s`, `sample_rate`, `size_bytes`)는 complete 전에는 바뀌어도 된다. 새 값으로 바꿔 두고, complete 때 새 `sample_rate`로 파일을 검사한다(3.4).
  - complete를 받은 뒤에는 파일 정보도 처음과 같아야 한다. 같으면 처음부터 다시 보내는 재시도로 보고 URL을 준다.
- 다른 세션에 이미 있는 `recording_id`는 409다. 녹음 ID는 녹음마다 새로 만든다.
- 한 요청 안에 409인 녹음이 하나라도 있으면 그 요청의 녹음은 하나도 등록하지 않는다.

오류: 404 `SESSION_NOT_FOUND`, 409 `ID_CONFLICT`(같은 `recording_id`에 다른 내용), 422

### 4.4 세션 완료 `POST /v1/sessions/{session_id}/complete`

모든 녹음을 올린 뒤 보낸다. 서버는 녹음이 모두 올라왔는지 확인하고 202를 돌려준 다음, 서버 지표를 따로 계산한다.

| 필드 | 형식 | 설명 |
|---|---|---|
| `ended_at` | 시각 | 세션 끝난 시각 |
| `device_measurements` | 목록(선택, 기본 빈 목록) | 기기에서 계산한 지표(7.1) |
| `game_runs` | 목록(선택, 기본 빈 목록) | 게임 판 기록(7.2). 반복 측정·자동 게인 조절 시험 세션은 비워 둔다 |
| `client_meta` | JSON 객체(선택) | 부가 정보 |

<!-- example: CompleteIn -->
```json
{
  "ended_at": "2026-11-20T18:41:00+09:00",
  "device_measurements": [
    {"metric": "max_phonation_time", "value": 14.0, "unit": "s", "low_reliability": false},
    {"metric": "f0_median", "value": 181.5, "unit": "Hz", "low_reliability": false},
    {"metric": "noise_floor", "value": -61.0, "unit": "dBFS", "low_reliability": false}
  ],
  "game_runs": [
    {
      "game_run_id": "6ecdaf48-7b9a-4fbc-936c-ba4b8e7fcd5a",
      "game": "breath_hold",
      "level": 3,
      "round_index": 1,
      "started_at": "2026-11-20T18:30:00+09:00",
      "params": {"target_s": 8},
      "success_rate_pct": 75.0,
      "decision": "up",
      "decision_reason": "성공률 70% 이상",
      "result": {"best_s": 9.1}
    }
  ],
  "client_meta": {"retry_count": 0}
}
```

응답 202

<!-- example: CompleteAccepted -->
```json
{"session_id": "8c41e7d2-5b3a-4f19-a6e2-9d0c7b1f3a58", "status": "processing"}
```

- 이미 처리 중이거나 끝난 세션에 다시 보내면, 중복 저장하지 않고 지금 상태를 돌려준다. 이때 요청 본문은 저장하지 않는다.
- 검사 순서. 앞에서 걸리면 뒤는 검사하지 않는다. 오류가 나면 아무것도 저장하지 않고 세션은 `open`으로 남으므로, 고친 뒤 다시 보내면 된다.
  1. 세션이 있는지(404)
  2. `game` 녹음의 `game_run_id`가 모두 `game_runs`에 있는지, `game_run_id`가 다른 세션에서 쓰이지 않았는지(422 `VALIDATION_ERROR`)
  3. 업로드 URL을 받은 녹음이 모두 S3에 올라왔는지(409 `MISSING_RECORDINGS`)
  4. 올라온 WAV가 규격에 맞는지(422 `INVALID_RECORDING_FORMAT`)
- 검사 대상은 이 세션에서 업로드 URL을 받은 녹음 전체다. 올리지 않을 녹음은 업로드 URL을 요청하지 않는다.
- 오류: 404 `SESSION_NOT_FOUND`, 409 `MISSING_RECORDINGS`(3.3), 422 `VALIDATION_ERROR`(3.2), 422 `INVALID_RECORDING_FORMAT`(3.4)

### 4.5 처리 상태와 서버 지표 `GET /v1/sessions/{session_id}`

complete 뒤 `status`가 `done`이 될 때까지 가끔 확인한다. 서버 계산에는 시간이 걸릴 수 있으므로 몇 초 간격이면 충분하다.

응답 200

<!-- example: SessionDetail -->
```json
{
  "id": "8c41e7d2-5b3a-4f19-a6e2-9d0c7b1f3a58",
  "user_id": "3f9b2c1e-7a4d-4e8b-9c2f-1a6d5e8b7c40",
  "purpose": "training",
  "started_at": "2026-11-20T09:15:00Z",
  "ended_at": "2026-11-20T09:41:00Z",
  "device_model": "SM-S921N",
  "os_version": "Android 16",
  "app_version": "0.1.0",
  "input_device": "wired_headset",
  "sample_rate": 48000,
  "status": "done",
  "is_baseline": true,
  "server_measurements": [
    {"metric": "max_phonation_time", "value": 12.4, "unit": "s", "low_reliability": false, "algorithm_version": "0.0.0-example"},
    {"metric": "loudness_decay_slope", "value": -1.8, "unit": "dB/s", "low_reliability": false, "algorithm_version": "0.0.0-example"},
    {"metric": "mean_level_change", "value": 0.0, "unit": "dB", "low_reliability": false, "algorithm_version": "0.0.0-example"},
    {"metric": "articulation_rate", "value": 4.1, "unit": "syllables/s", "low_reliability": false, "algorithm_version": "0.0.0-example"},
    {"metric": "speech_rate", "value": 3.5, "unit": "syllables/s", "low_reliability": false, "algorithm_version": "0.0.0-example"},
    {"metric": "pause_ratio", "value": 14.6, "unit": "%", "low_reliability": false, "algorithm_version": "0.0.0-example"},
    {"metric": "f0_sd_semitones", "value": 2.3, "unit": "semitones", "low_reliability": false, "algorithm_version": "0.0.0-example"},
    {"metric": "pitch_range_semitones", "value": 7.8, "unit": "semitones", "low_reliability": false, "algorithm_version": "0.0.0-example"},
    {"metric": "sustained_level", "value": -24.5, "unit": "dBFS", "low_reliability": false, "algorithm_version": "0.0.0-example"},
    {"metric": "f0_median", "value": 182.0, "unit": "Hz", "low_reliability": false, "algorithm_version": "0.0.0-example"},
    {"metric": "noise_floor", "value": -62.0, "unit": "dBFS", "low_reliability": false, "algorithm_version": "0.0.0-example"}
  ],
  "error": null
}
```

- `server_measurements`: 서버가 WAV에서 계산한 **공식 값**. 계산하지 못한 지표는 목록에 없다.
- `low_reliability`: 녹음 품질 때문에 믿기 어려운 값이다. 예: 발성이 너무 짧음, 유성 구간이 너무 적음
- `algorithm_version`: 계산 방법의 버전. 값은 같은 버전끼리만 비교한다.
- `error`: `status`가 `failed`일 때 `{"code", "message"}`, 아니면 `null`

오류: 404 `SESSION_NOT_FOUND`, 422

### 4.6 진전도 `GET /v1/users/{user_id}/history`

응답 형식과 판정 규칙은 8장에 있다. 오류: 404 `USER_NOT_FOUND`, 422

### 4.7 지표 정의 `GET /v1/metrics`

응답 200. 지표 11개(진전도 지표 8개, 보조 지표 3개)가 늘 같은 순서로 나온다.

<!-- example: MetricDefinitions -->
```json
{
  "metrics": [
    {"metric": "max_phonation_time", "unit": "s", "desired_direction": "up", "mdc": null},
    {"metric": "loudness_decay_slope", "unit": "dB/s", "desired_direction": "up", "mdc": null},
    {"metric": "mean_level_change", "unit": "dB", "desired_direction": "none", "mdc": null},
    {"metric": "articulation_rate", "unit": "syllables/s", "desired_direction": "none", "mdc": null},
    {"metric": "speech_rate", "unit": "syllables/s", "desired_direction": "none", "mdc": null},
    {"metric": "pause_ratio", "unit": "%", "desired_direction": "none", "mdc": null},
    {"metric": "f0_sd_semitones", "unit": "semitones", "desired_direction": "up", "mdc": null},
    {"metric": "pitch_range_semitones", "unit": "semitones", "desired_direction": "up", "mdc": null},
    {"metric": "sustained_level", "unit": "dBFS", "desired_direction": "none", "mdc": null},
    {"metric": "f0_median", "unit": "Hz", "desired_direction": "none", "mdc": null},
    {"metric": "noise_floor", "unit": "dBFS", "desired_direction": "none", "mdc": null}
  ]
}
```

- `desired_direction`: 지표가 어느 쪽으로 움직이길 바라는지(`up`, `down`, `none`). 서버는 좋아짐·나빠짐을 판정하지 않는다. 화면에서 해석할 때 참고하는 값이다. 말속도 지표는 `none`이다.
- `mdc`: 최소 측정 가능 변화량(`value`, `scale`). 반복 측정 분석으로 정해지기 전에는 `null`이다.

### 4.8 녹음 열람 URL `GET /v1/recordings/{recording_id}/url`

응답 200. URL은 10분 동안 유효하다.

<!-- example: RecordingUrl -->
```json
{
  "recording_id": "4cab8d26-5f7e-4d9c-b14a-9e2f6c5dab38",
  "url": "https://s3.example.com/example/4cab8d26-5f7e-4d9c-b14a-9e2f6c5dab38.wav?X-Amz-Signature=example",
  "expires_at": "2026-11-20T09:56:00Z"
}
```

- 열람할 수 있는 녹음은 complete에서 업로드가 확인된 녹음이다. complete 전이면 404다.
- 보관 기한은 S3에 올라간 날부터 365일이다. 지나면 404다.

오류: 404 `RECORDING_NOT_FOUND`(없음, complete 전, 보관 기한이 지남), 422

### 4.9 상태 확인 `GET /health`

앱 키가 필요 없다. 응답 200 `{"status": "ok"}`

---

## 5. 녹음 파일 규격

### 5.1 형식

- 모노, 16비트 PCM WAV
- 샘플레이트: 16000, 44100, 48000 Hz 중 하나
- 파일 하나의 최대 길이: 120초
- 측정 항목마다 별도 파일로 올린다.
- 앱은 녹음 앞뒤를 자르지 않는다. 발성·발화 구간은 서버가 찾는다.
- 앱은 음량 정규화, 잡음 제거, 압축을 하지 않는다.
- 저장 위치와 파일 이름은 서버가 정한다. 앱은 받은 URL로 올리기만 한다.

### 5.2 녹음 종류(`kind`)

| `kind` | 언제 | 꼭 넣는 필드 |
|---|---|---|
| `calibration_glide` | 첫 회차 1개. 낮은 소리에서 높은 소리로 미끄러지듯 "아" | 없음 |
| `noise_floor` | 매 회차 1개. 조용히 약 2초 | 없음 |
| `mpt_trial` | 매 회차 2~3개. 편한 크기로 "아"를 최대한 길게 | `trial_no`(1부터) |
| `eval_sentence` | 매 회차 1개. 평가 전용 문장 낭독 | `sentence_id`(지금은 `s1`만) |
| `game` | 선택. 게임 중 녹음 | `game_run_id` |
| `agc_tone` | `agc_test` 세션 전용. 시험 소리 3개를 재생하며 하나씩 녹음(9.3) | `agc_signal` |

종류마다 꼭 넣는 필드가 빠져도 422이고, 다른 종류의 필드를 넣어도 422다. 예: `noise_floor`에 `trial_no`를 넣으면 422

---

## 6. 업로드 순서와 재시도

1. 사용자 등록(처음 한 번), 세션 등록
2. 업로드 URL을 받아, 파일마다 응답의 `method`, `url`, `headers`대로 올린다.
3. 모두 올렸으면 complete를 보낸다.

재시도
- URL이 만료되면 업로드 URL을 다시 요청한다.
- complete가 409를 돌려주면 `missing_recording_ids`의 파일만 다시 올리고 complete를 다시 보낸다.
- 네트워크가 끊기면 처음부터 다시 해도 된다. 같은 요청은 같은 결과를 내므로(2.6) 중복으로 저장되지 않는다.
- 업로드는 기기에 보관해 두었다가, 연결이 될 때 대기열에서 보내는 방식을 권한다.

---

## 7. 기기 지표와 게임 기록

### 7.1 기기 지표 `device_measurements`

| 필드 | 형식 | 설명 |
|---|---|---|
| `metric` | 아래 11개 중 하나 | 지표 이름 |
| `value` | 유한한 숫자 | 값 |
| `unit` | 지표의 단위 | 아래 표와 같아야 한다. 다르면 422 |
| `low_reliability` | 참·거짓(선택, 기본 거짓) | 기기가 판단한 신뢰 낮음 |

- 같은 지표는 한 세션에 한 번만 보낸다.
- 서버는 기기 지표를 **저장만** 한다. 진전도와 변화 판정에는 서버가 WAV에서 계산한 값만 쓴다.
- 앱을 다시 설치했을 때 난이도 기준값을 복구할 수 있도록, 진전도 응답에 최근 3회차의 기기 값이 들어 있다(8장).

| `metric` | 단위 | 뜻 | 구분 |
|---|---|---|---|
| `max_phonation_time` | `s` | 최대발성지속시간 | 진전도 |
| `loudness_decay_slope` | `dB/s` | 음량 감쇠 기울기 | 진전도 |
| `mean_level_change` | `dB` | 평균 음량 변화 | 진전도 |
| `articulation_rate` | `syllables/s` | 조음속도 | 진전도 |
| `speech_rate` | `syllables/s` | 전체 말속도 | 진전도 |
| `pause_ratio` | `%` | 무음 비율 | 진전도 |
| `f0_sd_semitones` | `semitones` | 기본주파수 반음 표준편차 | 진전도 |
| `pitch_range_semitones` | `semitones` | 음도 범위 | 진전도 |
| `sustained_level` | `dBFS` | "아" 음량 | 보조 |
| `f0_median` | `Hz` | 기본주파수 중앙값 | 보조 |
| `noise_floor` | `dBFS` | 잡음 바닥 | 보조 |

정의와 기기 계산 권장 방법은 [`metrics-spec.md`](metrics-spec.md)에 있다.

### 7.2 게임 기록 `game_runs`

| 필드 | 형식 | 설명 |
|---|---|---|
| `game_run_id` | UUID v4 | 게임 판 ID. 한 요청 안에서 겹치면 422 |
| `game` | 영문 소문자 snake_case(64자 이하) | 게임 이름. 목록은 앱 팀이 정한다 |
| `level` | 정수(1부터, 상한 없음) | 단계 |
| `round_index` | 정수(1부터) | 판 번호 |
| `started_at` | 시각 | 판 시작 시각 |
| `params` | JSON 객체(선택) | 게임별 설정. 서버는 저장만 한다 |
| `success_rate_pct` | 숫자(0~100) | 판 성공률(%). **0~1 비율이 아니다.** 80%는 `80` |
| `decision` | `up` / `down` / `hold` / `deferred` | 다음 단계 판단(올림 / 내림 / 유지 / 판정 보류) |
| `decision_reason` | 문자열(선택, 500자 이하) | 판단 이유 |
| `result` | JSON 객체(선택) | 게임별 결과. 서버는 저장만 한다 |

---

## 8. 진전도 응답

### 8.1 규칙

- **포함 대상**: `purpose=training`이고 서버 처리가 끝난 세션만 넣는다.
- **기준선**: 사용자의 첫 완료 회차 서버 값이다. 같은 알고리즘 버전의 값끼리만 비교한다.
- **지표 목록**: 진전도 지표 8개를 값이 없어도 빼지 않고, 항상 7.1 표의 순서로 모두 넣는다. 보조 지표는 넣지 않는다.

지표마다 들어가는 필드

| 필드 | 설명 | 값이 없을 때 |
|---|---|---|
| `metric`, `unit`, `desired_direction` | 지표 이름, 단위, 바람직한 방향 | 항상 있음 |
| `baseline` | 기준선 회차의 `session_id`, `value` | `null` |
| `series` | 회차별 `session_id`, `started_at`, `value`, `low_reliability`(오래된 회차부터) | 빈 목록 |
| `latest` | 최근 회차의 `value`와 `change` | `null` |
| `mdc` | 최소 측정 가능 변화량 `value`, `scale`(`raw` / `log`) | `null`(아직 정해지지 않음) |
| `status` | 변화 판정(8.2) | `no_data` |

### 8.2 변화량과 판정

변화량
- `mdc.scale`이 `raw`이거나 `mdc`가 없을 때: Δ(변화량) = x_latest(최근 값) − x_baseline(기준선 값). 단위는 지표 단위다.
- `mdc.scale`이 `log`일 때: Δ%(퍼센트 변화) = (x_latest(최근 값) ÷ x_baseline(기준선 값) − 1) × 100. 단위는 %다.

`status`는 위에서부터 차례로 보고, 처음 맞는 것으로 정한다.

| 순서 | 조건 | `status` |
|---|---|---|
| 1 | 값이 없음 | `no_data` |
| 2 | 최근 값이 신뢰 낮음 | `low_reliability` |
| 3 | 최소 측정 가능 변화량이 아직 없음 | `pending_mdc` |
| 4 | \|변화량\| < 최소 측정 가능 변화량 | `no_change` |
| 5 | 그 밖 | `increase`(늘어남) 또는 `decrease`(줄어듦) |

`increase`와 `decrease`는 방향만 나타낸다. 좋아졌는지는 `desired_direction`과 함께 해석한다.

### 8.3 예시

아래 예시는 판정 종류를 모두 보여주려고 만든 값이다.
- `pitch_range_semitones`는 값이 없는 경우(`no_data`)를 보여준다.
- `articulation_rate`는 로그 척도라서 `change`가 퍼센트다.

<!-- example: History -->
```json
{
  "user_id": "3f9b2c1e-7a4d-4e8b-9c2f-1a6d5e8b7c40",
  "metrics": [
    {
      "metric": "max_phonation_time",
      "unit": "s",
      "desired_direction": "up",
      "baseline": {"session_id": "8c41e7d2-5b3a-4f19-a6e2-9d0c7b1f3a58", "value": 12.4},
      "series": [
        {"session_id": "8c41e7d2-5b3a-4f19-a6e2-9d0c7b1f3a58", "started_at": "2026-11-20T09:15:00Z", "value": 12.4, "low_reliability": false},
        {"session_id": "5d2e9c47-1a3b-4f6e-8d7c-2b4a6e8f0a12", "started_at": "2026-11-27T09:15:00Z", "value": 14.9, "low_reliability": false}
      ],
      "latest": {"value": 14.9, "change": 2.5},
      "mdc": {"value": 2.0, "scale": "raw"},
      "status": "increase"
    },
    {
      "metric": "loudness_decay_slope",
      "unit": "dB/s",
      "desired_direction": "up",
      "baseline": {"session_id": "8c41e7d2-5b3a-4f19-a6e2-9d0c7b1f3a58", "value": -1.8},
      "series": [
        {"session_id": "8c41e7d2-5b3a-4f19-a6e2-9d0c7b1f3a58", "started_at": "2026-11-20T09:15:00Z", "value": -1.8, "low_reliability": false},
        {"session_id": "5d2e9c47-1a3b-4f6e-8d7c-2b4a6e8f0a12", "started_at": "2026-11-27T09:15:00Z", "value": -1.5, "low_reliability": false}
      ],
      "latest": {"value": -1.5, "change": 0.3},
      "mdc": {"value": 0.9, "scale": "raw"},
      "status": "no_change"
    },
    {
      "metric": "mean_level_change",
      "unit": "dB",
      "desired_direction": "none",
      "baseline": {"session_id": "8c41e7d2-5b3a-4f19-a6e2-9d0c7b1f3a58", "value": 0.0},
      "series": [
        {"session_id": "8c41e7d2-5b3a-4f19-a6e2-9d0c7b1f3a58", "started_at": "2026-11-20T09:15:00Z", "value": 0.0, "low_reliability": false},
        {"session_id": "5d2e9c47-1a3b-4f6e-8d7c-2b4a6e8f0a12", "started_at": "2026-11-27T09:15:00Z", "value": 1.2, "low_reliability": false}
      ],
      "latest": {"value": 1.2, "change": 1.2},
      "mdc": null,
      "status": "pending_mdc"
    },
    {
      "metric": "articulation_rate",
      "unit": "syllables/s",
      "desired_direction": "none",
      "baseline": {"session_id": "8c41e7d2-5b3a-4f19-a6e2-9d0c7b1f3a58", "value": 4.1},
      "series": [
        {"session_id": "8c41e7d2-5b3a-4f19-a6e2-9d0c7b1f3a58", "started_at": "2026-11-20T09:15:00Z", "value": 4.1, "low_reliability": false},
        {"session_id": "5d2e9c47-1a3b-4f6e-8d7c-2b4a6e8f0a12", "started_at": "2026-11-27T09:15:00Z", "value": 4.3, "low_reliability": false}
      ],
      "latest": {"value": 4.3, "change": 4.9},
      "mdc": {"value": 12.0, "scale": "log"},
      "status": "no_change"
    },
    {
      "metric": "speech_rate",
      "unit": "syllables/s",
      "desired_direction": "none",
      "baseline": {"session_id": "8c41e7d2-5b3a-4f19-a6e2-9d0c7b1f3a58", "value": 3.5},
      "series": [
        {"session_id": "8c41e7d2-5b3a-4f19-a6e2-9d0c7b1f3a58", "started_at": "2026-11-20T09:15:00Z", "value": 3.5, "low_reliability": false},
        {"session_id": "5d2e9c47-1a3b-4f6e-8d7c-2b4a6e8f0a12", "started_at": "2026-11-27T09:15:00Z", "value": 3.8, "low_reliability": false}
      ],
      "latest": {"value": 3.8, "change": 0.3},
      "mdc": null,
      "status": "pending_mdc"
    },
    {
      "metric": "pause_ratio",
      "unit": "%",
      "desired_direction": "none",
      "baseline": {"session_id": "8c41e7d2-5b3a-4f19-a6e2-9d0c7b1f3a58", "value": 14.6},
      "series": [
        {"session_id": "8c41e7d2-5b3a-4f19-a6e2-9d0c7b1f3a58", "started_at": "2026-11-20T09:15:00Z", "value": 14.6, "low_reliability": false},
        {"session_id": "5d2e9c47-1a3b-4f6e-8d7c-2b4a6e8f0a12", "started_at": "2026-11-27T09:15:00Z", "value": 11.0, "low_reliability": true}
      ],
      "latest": {"value": 11.0, "change": -3.6},
      "mdc": {"value": 5.0, "scale": "raw"},
      "status": "low_reliability"
    },
    {
      "metric": "f0_sd_semitones",
      "unit": "semitones",
      "desired_direction": "up",
      "baseline": {"session_id": "8c41e7d2-5b3a-4f19-a6e2-9d0c7b1f3a58", "value": 2.3},
      "series": [
        {"session_id": "8c41e7d2-5b3a-4f19-a6e2-9d0c7b1f3a58", "started_at": "2026-11-20T09:15:00Z", "value": 2.3, "low_reliability": false},
        {"session_id": "5d2e9c47-1a3b-4f6e-8d7c-2b4a6e8f0a12", "started_at": "2026-11-27T09:15:00Z", "value": 1.9, "low_reliability": false}
      ],
      "latest": {"value": 1.9, "change": -0.4},
      "mdc": {"value": 0.3, "scale": "raw"},
      "status": "decrease"
    },
    {
      "metric": "pitch_range_semitones",
      "unit": "semitones",
      "desired_direction": "up",
      "baseline": null,
      "series": [],
      "latest": null,
      "mdc": null,
      "status": "no_data"
    }
  ],
  "recent_device_measurements": [
    {
      "session_id": "8c41e7d2-5b3a-4f19-a6e2-9d0c7b1f3a58",
      "started_at": "2026-11-20T09:15:00Z",
      "measurements": [
        {"metric": "max_phonation_time", "value": 11.8, "unit": "s", "low_reliability": false},
        {"metric": "f0_median", "value": 178.0, "unit": "Hz", "low_reliability": false},
        {"metric": "noise_floor", "value": -60.5, "unit": "dBFS", "low_reliability": false}
      ]
    },
    {
      "session_id": "5d2e9c47-1a3b-4f6e-8d7c-2b4a6e8f0a12",
      "started_at": "2026-11-27T09:15:00Z",
      "measurements": [
        {"metric": "max_phonation_time", "value": 14.2, "unit": "s", "low_reliability": false},
        {"metric": "f0_median", "value": 181.5, "unit": "Hz", "low_reliability": false},
        {"metric": "noise_floor", "value": -61.0, "unit": "dBFS", "low_reliability": false}
      ]
    }
  ]
}
```

---

## 9. 세션 구조와 앱 쪽 필수 사항

### 9.1 세션 구조

| 회차 | 순서 |
|---|---|
| 첫 회차 | 캘리브레이션(`calibration_glide`) → 측정 블록 → 게임 |
| 이후 회차 | 측정 블록 → 게임 |

- **측정 블록**: 조용히 약 2초(`noise_floor`) → "아" 최대한 길게 2~3회(`mpt_trial`) → 평가 전용 문장 낭독(`eval_sentence`)
- 사후 측정은 없다.

### 9.2 앱 팀에 부탁하는 것

- **측정 블록 녹음·업로드를 게임보다 먼저 구현해 주세요.** 반복 측정(9.4)은 서로 다른 날이 필요해 최소 1주가 걸리므로, 이 부분이 늦으면 지표 검증 일정이 밀린다.
- 녹음은 5장 규격을 따른다.
- 운영체제(OS)의 음성 처리(자동 게인 조절, 잡음 제거)는 끌 수 있으면 끈다.

### 9.3 자동 게인 조절 시험(`purpose=agc_test`)

기기가 녹음 음량을 스스로 조절하는지 확인한다. 조절하면 음량 지표를 믿을 수 없다.

1. 시험 소리 파일 3개를 만든다. 만드는 방법과 준비는 [`backend/fixtures/agc/README.md`](../backend/fixtures/agc/README.md)에 있다.
2. `agc_test` 세션 하나에서, 다른 기기의 스피커로 시험 소리를 하나씩 재생하며 녹음한다. 소리 하나가 녹음 하나다.
3. 녹음 3개를 `kind=agc_tone`으로 올리고, 녹음마다 `agc_signal`에 재생한 소리를 적는다.
4. 판정은 서버 분석이 한다.

업로드 URL 요청 예시

<!-- example: UploadUrlsIn -->
```json
{
  "recordings": [
    {"recording_id": "7a1e3c58-9d2b-4e6f-8a0c-1b3d5f7e9a21", "kind": "agc_tone", "agc_signal": "agc_steps", "duration_s": 24.0, "sample_rate": 48000, "size_bytes": 2304044},
    {"recording_id": "8b2f4d69-ae3c-4f70-9b1d-2c4e6a8f0b32", "kind": "agc_tone", "agc_signal": "agc_hold", "duration_s": 33.0, "sample_rate": 48000, "size_bytes": 3168044},
    {"recording_id": "9c3a5e7a-bf4d-4a81-ac2e-3d5f7b9a1c43", "kind": "agc_tone", "agc_signal": "agc_toggle", "duration_s": 23.0, "sample_rate": 48000, "size_bytes": 2208044}
  ]
}
```

### 9.4 반복 측정(`purpose=retest`)

- 팀원 4명이 서로 다른 날 3회씩 측정 블록만 진행하고, `retest` 세션으로 보낸다.
- 실제 앱의 녹음 경로로 해야 최소 측정 가능 변화량이 의미가 있다.

---

## 10. 버전과 변경 기록

- 버전은 이 문서 맨 위와 `/openapi.json`의 `info.version`에 있다.
- 바뀌면 [`api-changelog.md`](api-changelog.md)에 날짜, 내용, 앱에 미치는 영향을 적는다.
- 기존 앱이 깨지는 변경(필드 삭제, 이름 변경, 의미 변경)은 피한다. 불가피하면 이유와 옮겨 가는 방법을 함께 적는다.
