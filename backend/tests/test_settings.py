from pathlib import Path

import pytest

from app.settings import OPERATING_VARIABLES, Settings, missing_operating_variables, require_operating_settings

VARIABLES = [
    "DATABASE_URL",
    "AWS_REGION",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "S3_BUCKET",
    "APP_API_KEY",
    "TEST_DATABASE_URL",
]
SECRET_VARIABLES = [
    "DATABASE_URL",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "APP_API_KEY",
    "TEST_DATABASE_URL",
]


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for name in VARIABLES:
        monkeypatch.delenv(name, raising=False)


def test_reads_environment_variables(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pw@db.example.com:5432/postgres")
    monkeypatch.setenv("S3_BUCKET", "dyspeech-recordings")

    settings = Settings(_env_file=None)

    assert settings.database_url.get_secret_value() == "postgresql://user:pw@db.example.com:5432/postgres"
    assert settings.s3_bucket == "dyspeech-recordings"


def test_reads_env_file_with_windows_line_endings(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_bytes(b'AWS_REGION=ap-northeast-2\r\nAPP_API_KEY="demo-key"\r\n')

    settings = Settings(_env_file=env_file)

    assert settings.aws_region == "ap-northeast-2"
    assert settings.app_api_key.get_secret_value() == "demo-key"


def test_environment_variable_overrides_env_file(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("S3_BUCKET=from-file\n", encoding="utf-8")
    monkeypatch.setenv("S3_BUCKET", "from-environment")

    assert Settings(_env_file=env_file).s3_bucket == "from-environment"


def test_missing_and_empty_values_are_none(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("DATABASE_URL=\nS3_BUCKET=\n", encoding="utf-8")

    settings = Settings(_env_file=env_file)

    assert settings.model_dump() == {name.lower(): None for name in VARIABLES}


def test_secret_values_are_hidden(monkeypatch):
    for name in SECRET_VARIABLES:
        monkeypatch.setenv(name, f"secret-value-of-{name}")

    settings = Settings(_env_file=None)
    outputs = [repr(settings), str(settings), settings.model_dump_json()]

    for name in SECRET_VARIABLES:
        assert getattr(settings, name.lower()).get_secret_value() == f"secret-value-of-{name}"
        for output in outputs:
            assert f"secret-value-of-{name}" not in output


OPERATING_VALUES = {
    "DATABASE_URL": "postgresql://user:secret-password@db.example.com:5432/postgres",
    "AWS_REGION": "ap-northeast-2",
    "AWS_ACCESS_KEY_ID": "AKIAEXAMPLE",
    "AWS_SECRET_ACCESS_KEY": "secret-aws-key",
    "S3_BUCKET": "dyspeech-recordings",
    "APP_API_KEY": "secret-app-key",
}


def test_require_operating_settings_passes_when_all_are_set(monkeypatch):
    for name, value in OPERATING_VALUES.items():
        monkeypatch.setenv(name, value)

    settings = Settings(_env_file=None)

    assert missing_operating_variables(settings) == []
    require_operating_settings(settings)


def test_require_operating_settings_names_missing_ones_without_values(monkeypatch):
    for name, value in OPERATING_VALUES.items():
        if name not in ("AWS_REGION", "S3_BUCKET"):
            monkeypatch.setenv(name, value)

    with pytest.raises(RuntimeError) as caught:
        require_operating_settings(Settings(_env_file=None))

    message = str(caught.value)
    assert "AWS_REGION, S3_BUCKET" in message
    assert not any(value in message for value in OPERATING_VALUES.values())


def test_every_operating_variable_is_in_env_example():
    example = (Path(__file__).resolve().parents[1] / ".env.example").read_text(encoding="utf-8")

    for name in OPERATING_VARIABLES:
        assert f"\n{name}=" in example
