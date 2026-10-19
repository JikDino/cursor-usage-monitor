import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("start", Path(__file__).resolve().parents[1] / "start.py")
start = importlib.util.module_from_spec(spec)
spec.loader.exec_module(start)


def test_parse_account():
    assert start.parse_account("50123456-01") == ("50123456", "01")
    assert start.parse_account("5012345622") == ("50123456", "22")
    assert start.parse_account("50123456") == ("50123456", "01")
    with pytest.raises(ValueError):
        start.parse_account("123")


def test_write_env_keeps_template_and_fills_values(tmp_path, monkeypatch):
    example = tmp_path / ".env.example"
    example.write_text("# 주석\nKIS_APP_KEY=\nKIS_ENV=paper\nMAX_POSITIONS=5\n", encoding="utf-8")
    monkeypatch.setattr(start, "ENV_FILE", tmp_path / ".env")
    monkeypatch.setattr(start, "ENV_EXAMPLE", example)

    assert start.needs_setup()
    start.write_env({"KIS_APP_KEY": "abc", "KIS_APP_SECRET": "s", "KIS_ACCOUNT_NO": "50123456"})
    text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert text.startswith("# 주석\nKIS_APP_KEY=abc\nKIS_ENV=paper\nMAX_POSITIONS=5\n")
    assert "KIS_APP_SECRET=s" in text
    assert not start.needs_setup()

    start.write_env({"KIS_ENV": "real"})
    env = start.read_env()
    assert env["KIS_ENV"] == "real" and env["KIS_APP_KEY"] == "abc"
