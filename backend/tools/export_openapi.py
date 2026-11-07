"""FastAPI가 만든 OpenAPI 문서를 docs/openapi.json으로 내보낸다.

backend 폴더에서 실행한다.
    python -m tools.export_openapi          파일을 새로 쓴다
    python -m tools.export_openapi --check  파일이 코드와 같은지만 확인한다(다르면 종료 코드 1)

API 명세가 바뀌면 이 파일의 diff로 드러난다. 테스트도 파일이 코드와 같은지 확인한다.
"""

import argparse
import json
import sys
from pathlib import Path

from app.main import app

OUTPUT = Path(__file__).resolve().parents[2] / "docs" / "openapi.json"


def render() -> str:
    """
    FastAPI 앱의 OpenAPI 문서를 JSON 문자열로 바꿈
    입력: 없음 (app.main의 FastAPI 앱)
    출력: 들여쓰기 2칸, 한글을 그대로 둔(ensure_ascii=False) JSON 문자열. 끝에 줄바꿈 하나
    알고리즘
    1. app.openapi()를 JSON으로 바꾸고 끝에 줄바꿈을 붙여 돌려줌
    """
    return json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n"


def main(argv: list[str] | None = None) -> int:
    """
    OpenAPI 문서를 docs/openapi.json으로 쓰거나, 파일이 코드와 같은지 확인함
    입력: argv (명령줄 인자 목록, 없으면 sys.argv. --check: 파일을 쓰지 않고 같은지만 확인)
    출력: 종료 코드(0 = 썼거나 같음, 1 = --check에서 파일이 없거나 다름). --check가 없으면 docs/openapi.json을 씀. 결과 문장을 표준 출력(다를 때는 표준 오류)에 씀
    알고리즘
    1. 명령줄 인자를 읽고 render로 문서 문자열을 만듦
    2. --check면 파일이 있고 내용이 같을 때 0, 아니면 다시 만드는 방법을 표준 오류에 쓰고 1을 돌려줌
    3. --check가 아니면 docs 폴더를 만들고, Windows에서도 diff가 생기지 않도록 줄바꿈을 \\n으로 고정해 UTF-8로 씀
    4. 쓴 크기(바이트)를 출력하고 0을 돌려줌
    """
    parser = argparse.ArgumentParser(description="OpenAPI 문서를 docs/openapi.json으로 내보낸다")
    parser.add_argument("--check", action="store_true", help="파일을 쓰지 않고, 코드와 같은지만 확인한다")
    args = parser.parse_args(argv)

    content = render()
    if args.check:
        if OUTPUT.exists() and OUTPUT.read_text(encoding="utf-8") == content:
            print("docs/openapi.json이 코드와 같습니다")
            return 0
        print("docs/openapi.json이 코드와 다릅니다. python -m tools.export_openapi로 다시 만드세요", file=sys.stderr)
        return 1

    OUTPUT.parent.mkdir(exist_ok=True)
    # Windows에서 실행해도 줄바꿈이 바뀌어 diff가 생기지 않도록 \n으로 고정한다.
    OUTPUT.write_text(content, encoding="utf-8", newline="\n")
    print(f"docs/openapi.json을 썼습니다({len(content.encode())} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
