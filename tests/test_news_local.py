"""The local runner reads only LLM_* settings, from the git-ignored .env, and never overrides."""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from engines import news_local as NLoc


def test_only_llm_settings_are_read_and_secrets_elsewhere_are_ignored(tmp_path):
    f = tmp_path / ".env"
    f.write_text('DATABASE_URL=postgresql://secret\n# c\nLLM_BASE_URL="http://127.0.0.1:8317/v1"\nLLM_API_KEY=abc\n', encoding="utf-8")
    env = {}
    got = NLoc.load_llm_settings(str(f), env)
    assert got == {"LLM_BASE_URL": "http://127.0.0.1:8317/v1", "LLM_API_KEY": "abc"}
    assert "DATABASE_URL" not in env


def test_an_already_set_value_wins(tmp_path):
    f = tmp_path / ".env"
    f.write_text("LLM_MODEL=from-file\n", encoding="utf-8")
    env = {"LLM_MODEL": "from-shell"}
    NLoc.load_llm_settings(str(f), env)
    assert env["LLM_MODEL"] == "from-shell"


def test_a_missing_file_is_not_an_error(tmp_path):
    assert NLoc.load_llm_settings(str(tmp_path / "nope.env"), {}) == {}


def test_without_all_three_settings_it_says_so_and_stops(monkeypatch, capsys):
    for k in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(NLoc, "load_llm_settings", lambda *a, **k: {})
    assert NLoc.main([]) == 2
    assert "must be set in .env" in capsys.readouterr().out


def test_the_key_is_never_committed():
    """The settings file is git-ignored; nothing tracked may contain the local access code."""
    import subprocess
    assert subprocess.run(["git", "check-ignore", ".env"], cwd=_ROOT, capture_output=True).returncode == 0
    tracked = subprocess.run(["git", "grep", "-lI", "8317"], cwd=_ROOT, capture_output=True, text=True).stdout.split()
    assert all(f.startswith(("tests/test_news_local.py", "engines/news_local.py")) for f in tracked), tracked
