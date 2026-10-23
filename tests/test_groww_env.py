import pytest

from app.cli import main
from app.groww_env import load_groww_credentials

VARS = ["GROWW_ACCESS_TOKEN", "GROWW_API_KEY", "GROWW_API_SECRET",
        "GROWW_TOTP_TOKEN", "GROWW_TOTP_SECRET"]


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for v in VARS:
        monkeypatch.delenv(v, raising=False)


def test_no_credentials(tmp_path):
    c = load_groww_credentials(tmp_path / "missing.env")
    assert c.auth_mode() is None


def test_env_file_api_key_pair_and_masking(tmp_path, monkeypatch):
    f = tmp_path / ".env"
    f.write_text("GROWW_API_KEY=k123\nGROWW_API_SECRET=topsecret\n")
    monkeypatch.setattr("os.environ", dict(__import__("os").environ))
    c = load_groww_credentials(f)
    assert c.auth_mode() == "api_key_secret"
    assert "topsecret" not in repr(c) and "k123" not in str(c)


def test_half_a_pair_is_incomplete(monkeypatch, tmp_path):
    monkeypatch.setenv("GROWW_API_KEY", "only-key")
    assert load_groww_credentials(tmp_path / "x").auth_mode() is None


def test_cli_never_prints_values(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("GROWW_ACCESS_TOKEN", "SUPERSECRETTOKEN")
    rc = main(["check-env", "--env-file", str(tmp_path / "none")])
    out = capsys.readouterr().out
    assert rc == 0 and "SUPERSECRETTOKEN" not in out and "access_token" in out
