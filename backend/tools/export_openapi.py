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
    return json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n"


def main(argv: list[str] | None = None) -> int:
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
