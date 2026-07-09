"""Unit tests for config env parsing / defaults / db_path (TESTING-STRATEGY §2.1)."""
import pathlib

import pytest


@pytest.fixture
def config(server_modules):
    return server_modules["config"]


# --- .env file parsing ------------------------------------------------------
def test_parse_env_file_handles_comments_quotes_inline(config, tmp_path):
    p = tmp_path / ".env.fable"
    p.write_text(
        "# a comment\n"
        "\n"
        "FABLE_DB=/tmp/x.db\n"
        'SHOPIFY_SHOP="buttons-bebe.myshopify.com"\n'
        "FABLE_BRAIN=mock   # inline comment\n"
        "NOEQUALSLINE\n"
        "   SPACED_KEY = spaced_value \n"
    )
    parsed = config._parse_env_file(p)
    assert parsed["FABLE_DB"] == "/tmp/x.db"
    assert parsed["SHOPIFY_SHOP"] == "buttons-bebe.myshopify.com"  # quotes stripped
    assert parsed["FABLE_BRAIN"] == "mock"  # inline comment stripped
    assert "NOEQUALSLINE" not in parsed
    assert parsed["SPACED_KEY"] == "spaced_value"


def test_parse_env_file_missing_returns_empty(config, tmp_path):
    assert config._parse_env_file(tmp_path / "does-not-exist.env") == {}


# --- resolution order: env var > file > default -----------------------------
def test_get_prefers_env_var(config, monkeypatch):
    monkeypatch.setenv("SUPPORT_EMAIL", "override@example.com")
    assert config.get("SUPPORT_EMAIL") == "override@example.com"


def test_get_empty_env_var_falls_through(config, monkeypatch):
    # an empty env var is treated as unset (falls through to file/default)
    monkeypatch.setenv("FABLE_BRAIN", "")
    assert config.get("FABLE_BRAIN") in ("mock",)  # from file/default


def test_get_falls_back_to_default_for_unknown_key(config, monkeypatch):
    monkeypatch.delenv("TOTALLY_UNKNOWN_KEY", raising=False)
    assert config.get("TOTALLY_UNKNOWN_KEY") == ""


def test_known_default_present(config):
    # SHOPIFY_BASE default from the API contract
    assert config.SHOPIFY_BASE == "http://127.0.0.1:9601"
    assert config.PORT == 9600


# --- db_path ----------------------------------------------------------------
def test_db_path_absolute_is_used_verbatim(config, monkeypatch, tmp_path):
    target = tmp_path / "sub" / "fable.db"
    monkeypatch.setenv("FABLE_DB", str(target))
    resolved = pathlib.Path(config.db_path())
    assert resolved == target
    assert resolved.parent.is_dir()  # parent auto-created


def test_db_path_relative_is_anchored_to_repo_root(config, monkeypatch):
    monkeypatch.setenv("FABLE_DB", "fable/server/data/fable.db")
    resolved = pathlib.Path(config.db_path())
    assert resolved.is_absolute()
    assert resolved.parts[-3:] == ("server", "data", "fable.db")
