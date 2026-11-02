# dySpeech

2026 2학기 산학 프로젝트

## 커밋 규칙

- FEAT: 추가 기능 추가
- FIX: 버그 수정
- DOCS: 문서 변경
- STYLE: 코드 형식, 세미콜론 응답 등 코드 변경이 없는 경우
- REFACTOR: 코드 리팩토링 (기능 변경 없음)
- TEST: 밴드된 테스트를 추가하거나 기존 테스트 수정
- CHORE: 빌드 프레임 또는 보조 도구 수정(라이브러리 추가 등)
- PERF: 성능 향상 관련 변경
- BUILD: 빌드 관련 파일 변경

---

여기부터 초기 세팅에 관한 내용입니다.

## 구조

- `frontend/` Expo SDK 58 앱 (TypeScript, 개발 빌드)
- `backend/` FastAPI 서버 (Python 3.13)

## 준비물

- Node.js 24 LTS (`frontend/.nvmrc`), Python 3.13
- Android Studio (Android SDK), 실제 스마트폰
- iOS는 Mac + Xcode 또는 EAS Build (Apple Developer 계정 필요)
  - 다만 iOS는 제가 android 사용자라 자세히 모릅니다.
  - 해당 설명서는 android 기준이니 참고 바랍니다.

## frontend

```bash
cd frontend
npm ci
npm run android   # 폰 USB로 연결 필요
```

이후 개발할 때는 `npm start`로 서버를 띄우고 폰에 설치된 dySpeech 앱으로 접속

- 패키지는 `npx expo install <패키지>`로 추가하고 `package-lock.json`을 함께 커밋해주세요
- 네이티브 패키지를 추가하거나 `app.json`을 바꾸면 `npm run android`
- `android/`, `ios/`는 빌드 때 생성되므로 커밋하지 않기. ignore 처리는 해두었으나 혹시 몰라 재기입합니다.
- 이후부터는 제가 초기 세팅하며 겪었던 에러입니다.
  <br><br>
- error type 1: PATH error
  - react-native-audio-api 빌드 스크립트가 Git Bash의 `mkdir`, `unzip`, `rm`을 쓰는데, 이 명령들이 들어 있는 Git 설치 폴더 안의 usr\bin 폴더가 PATH에 없으면 실패합니다. 따라서 PATH에 추가햐주세요.

- error type 2: 경로 길이 error
  - Windows에서는 C++ 빌드 경로가 260자를 넘어서 실패할 수 있습니다. 그럴 경우 다음 두가지를 진행해주세요.
  1.  긴 경로 허용
      - Win + R을 누르고 regedit을 입력하여 레지스트리 편집기 열기
      - 다음 경로로 이동: <br> `컴퓨터\HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Control\FileSystem`
      - `LongPathsEnabled` 값을 찾아 더블 클릭한 뒤, 데이터 값을 1로 변경
  2.  Android Studio SDK Manager → SDK Tools → Show Package Details → CMake 3.31 이상 설치

  3.  `frontend/android/local.properties`에 설치한 CMake 경로 지정

## 서버 (backend)

Windows:

```bash
cd backend
py -3.13 -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt
```

macOS: `python3.13 -m venv .venv && source .venv/bin/activate` 후 같은 `pip install`.

- 다시 말씀드리지만, 저는 window와 android를 사용해서 macOS나 iOS의 경우 정확히 모르니 참고해주시기 바랍니다.

- 테스트: `pytest -q`
- 실행: `uvicorn app.main:app --reload`
- 폰에서 접속할 때는 `uvicorn app.main:app --reload --host 0.0.0.0`으로 띄우고 PC의 내부 IP를 씁니다.
- PC 내부 IP는 ipconfig로 cmd에서 확인하실 수 있습니다. 추후 env.example에 있는 로컬 호스트 주소 변경 시 사용하시면 됩니다.
- 패키지를 추가할 때는 `requirements.txt`에 버전을 함께 기입해주세요.

### 추가 고려사항

- 프론트엔드 폴더 내의 gitignore 파일과 env 파일(추후 추가한다면)을 외부로 통합시켜야 하나 고민입니다.
  - env는 외부에서 사용하지 않을 것 같아 괜찮은데 ignore는 외부, 내부 둘 다 존재하니 개발 과정에서 헷갈리실 것 같아 그렇습니다.
- 다시 말씀드리지만, 저는 window & android를 사용해 오류나, 명령어가 다를 수 있으니 확인 부탁드립니다.
