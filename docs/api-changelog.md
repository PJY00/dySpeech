# API 명세 변경 기록

API 명세([`api.md`](api.md), [`openapi.json`](openapi.json))가 바뀔 때마다 여기에 적는다. 새 기록이 위로 온다.

## 규칙

- 버전은 `주.부.수` 세 자리다. 서버의 `/openapi.json`에서 `info.version`으로 볼 수 있다.
  - **부 번호**: 기존 앱이 깨지지 않는 변경. 선택 필드·응답 필드·엔드포인트·오류 코드 추가 등
  - **주 번호**: 기존 앱이 깨지는 변경. 필드 삭제, 이름 변경, 의미 변경. 피하는 것이 원칙이고, 불가피하면 이유와 옮겨 가는 방법을 함께 적는다.
  - **수 번호**: 동작이 바뀌지 않는 설명·예시 수정
- 기록마다 날짜, 버전, 바뀐 것, 앱에 미치는 영향을 적는다.
- `openapi.json`은 서버 코드에서 만든다. 코드와 다르면 서버 테스트가 실패하므로, 명세가 바뀌면 PR에 `openapi.json` diff가 함께 보인다.

---

## 예정

아직 버전을 올리지 않은 변경이다. 다음 버전을 낼 때 그 기록으로 옮긴다.

- **용어: 판 → 라운드(필드 이름 변경 없음)**: 게임의 한 차례를 문서와 설명에서 "라운드"라고 쓴다. 예: 게임 라운드 ID(`game_run_id`), 라운드 번호(`round_index`), 라운드 성공률(`success_rate_pct`). 지난 기록의 "판"은 그대로 둔다.
  - 앱 영향: 없음. 필드 이름과 동작은 그대로다.
- **서버 지표 계산**: 아직 적용되지 않았다. 응답 형식은 바뀌지 않는다.
  - complete 뒤 서버가 WAV에서 지표를 계산하고, 세션 `status`가 `done` 또는 `failed`가 된다.
  - `server_measurements`와 진전도(`baseline`, `series`, `latest`, `status`)에 값이 들어간다.
  - 앱 영향: 없음. 지금도 이 값들이 비어 있거나 `no_data`인 경우를 처리하고 있어야 한다.

---

## 0.2.0 (2026-11-07): 실제 저장과 업로드

임시 서버(예시 응답) 대신 실제로 저장하는 서버다. 0.1.0에서 "다음 단계부터 생기는 응답"으로 적어 둔 것을 구현했다.

- **추가**
  - complete의 422 `INVALID_RECORDING_FORMAT`([`api.md` 3.4](api.md#34-wav-규격-422)). `openapi.json`에 `InvalidRecordingsResponse`가 생겼고, complete의 422는 이것과 `ValidationErrorResponse` 중 하나다.
  - 모든 요청의 413 `PAYLOAD_TOO_LARGE`([`api.md` 3.5](api.md#35-요청-크기-상한-413))
- **실제 동작 시작**
  - 저장, 404·409 응답, S3에 실제로 올라가는 업로드 URL, 녹음 열람 URL
  - 서버 지표 계산은 아직 없다. `status`는 `processing`에 머물고, 진전도 지표는 모두 `no_data`다([`api.md` 1.1](api.md#11-지금-서버에서-아직-되지-않는-것)).
- **명확히 한 것**(0.1.0 규칙을 실제 서버에서 어떻게 지키는지)
  - 같은 `recording_id`로 업로드 URL을 다시 받을 때 complete 전에는 파일 정보(`duration_s`, `sample_rate`, `size_bytes`)가 바뀌어도 된다. WAV 규격 422 뒤 고친 파일을 같은 ID로 올리기 위해서다. complete 뒤에는 처음과 같아야 한다([`api.md` 4.3](api.md#43-업로드-url-발급-post-v1sessionssession_idupload-urls)).
  - 세션 등록을 다시 보낼 때 `client_meta`는 비교하지 않는다. 처음 받은 값을 둔다([`api.md` 2.6](api.md#26-같은-요청을-다시-보낼-때)).
  - complete의 검사 순서: 404 → 게임 판 확인(422 `VALIDATION_ERROR`) → 올라오지 않은 녹음(409) → WAV 규격(422). 오류가 나면 아무것도 저장하지 않고 세션은 `open`으로 남는다([`api.md` 4.4](api.md#44-세션-완료-post-v1sessionssession_idcomplete)).
  - `game` 녹음의 `game_run_id`가 complete의 `game_runs`에 없으면 422 `VALIDATION_ERROR`(경로 `body.game_runs`)다. 0.1.0에 적은 "같은 ID가 있어야 한다"를 검사한다.
  - 녹음 열람은 complete에서 업로드가 확인된 녹음만 된다. complete 전이면 404 `RECORDING_NOT_FOUND`다. 보관 기한은 S3에 올라간 날부터 365일이다.
- **앱 영향**
  - 업로드 URL이 실제 S3 주소다. 응답의 `headers`를 그대로 붙여 올린다. `Content-Type`을 빼거나 바꾸면 S3가 403으로 거부한다.
  - complete가 409·422를 돌려줄 수 있다. [`api.md` 3.3·3.4](api.md#3-오류)대로 고쳐 다시 보낸다.
  - 기존 필드와 이름은 바뀌지 않았다.

---

## 0.1.0 (2026-11-06): 첫 API 명세

2026-11-07 앱 팀에 공유했다. 임시 서버는 이 명세대로 예시 응답을 돌려준다.

- 엔드포인트
  - `/v1` 아래 8개: 사용자 등록, 세션 등록, 업로드 URL 발급, 세션 완료, 처리 상태, 진전도, 지표 정의, 녹음 열람 URL
  - `GET /health`는 앱 키 없이 쓴다.
- 공통 규칙
  - `X-App-Key` 인증
  - 시간대가 있는 ISO 8601 시각(응답은 UTC)
  - 앱이 만드는 UUID v4 ID
  - API 명세에 없는 필드는 422, 부가 정보는 `client_meta`
  - 같은 요청은 같은 결과
- 오류 형식: `{"error": {"code", "message"}}`. 422는 `fields[]`에 문제 필드의 경로와 이유, 409(빠진 녹음)는 `missing_recording_ids`를 담는다.
- 다음 단계부터 생기는 응답(서버 구현 전이지만 명세에 미리 포함): complete의 WAV 규격 422 `INVALID_RECORDING_FORMAT`, 모든 요청의 본문 크기 413 `PAYLOAD_TOO_LARGE`(1 MB)
- 녹음 종류별 필수 필드: `mpt_trial`은 `trial_no`, `eval_sentence`는 `sentence_id`, `game`은 `game_run_id`, `agc_tone`은 `agc_signal`(`agc_steps` / `agc_hold` / `agc_toggle`). 다른 종류의 필드를 넣으면 422다.
- 자동 게인 조절 시험: `agc_test` 세션 하나에 시험 소리 3개를 녹음 3개로 보낸다.
- 평가 전용 문장: `sentence_id`는 서버 설정에 있는 값(지금은 `s1`)만 받는다. 다른 값은 422다. 서버가 정답 음절 수를 알아야 말속도 지표를 계산할 수 있기 때문이다.
- 지표: 진전도 지표 8개와 보조 지표 3개(`sustained_level`, `f0_median`, `noise_floor`)
- 진전도: 진전도 지표 8개를 항상 같은 순서로 넣는다. 값이 없으면 `status`는 `no_data`다.
- 게임 기록: 판 성공률은 `success_rate_pct`(0~100)다.
