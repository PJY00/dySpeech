import json

from tools import export_openapi


def test_openapi_file_matches_code():
    assert export_openapi.OUTPUT.read_text(encoding="utf-8") == export_openapi.render(), (
        "계약이 바뀌었습니다. backend에서 python -m tools.export_openapi로 docs/openapi.json을 다시 만드세요"
    )


def test_check_mode_reports_difference(tmp_path, monkeypatch):
    stale = tmp_path / "openapi.json"
    stale.write_text(json.dumps({"openapi": "3.1.0"}), encoding="utf-8")
    monkeypatch.setattr(export_openapi, "OUTPUT", stale)

    assert export_openapi.main(["--check"]) == 1

    stale.write_text(export_openapi.render(), encoding="utf-8")
    assert export_openapi.main(["--check"]) == 0


def test_write_mode_uses_unix_line_endings(tmp_path, monkeypatch):
    output = tmp_path / "docs" / "openapi.json"
    monkeypatch.setattr(export_openapi, "OUTPUT", output)

    assert export_openapi.main([]) == 0
    assert b"\r\n" not in output.read_bytes()
    assert output.read_text(encoding="utf-8") == export_openapi.render()
