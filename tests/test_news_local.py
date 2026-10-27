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


class TestCatchingUpAfterThePcWasOff:
    def _s(self):
        import datetime
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        import db as dbmod
        eng = create_engine("sqlite:///:memory:")
        dbmod.Base.metadata.create_all(eng)
        return sessionmaker(bind=eng)(), dbmod, datetime

    def _art(self, s, dbmod, datetime, url, model, age_days, body="text"):
        now = datetime.datetime(2026, 10, 27, 12)
        s.add(dbmod.NewsArticle(url=url, source="x", title="t", body_text=body, triage_status="extracted",
                                triage_model=model, fetched_utc=now - datetime.timedelta(days=age_days)))
        s.commit()
        return now

    def test_recent_rule_read_articles_go_back_to_the_model(self):
        s, dbmod, dt = self._s()
        now = self._art(s, dbmod, dt, "a", "rules-v1", 1)
        assert NLoc.lift_rule_reads(s, now) == 1
        assert s.query(dbmod.NewsArticle).one().triage_status == "untriaged"

    def test_articles_the_model_already_read_are_left_alone(self):
        s, dbmod, dt = self._s()
        now = self._art(s, dbmod, dt, "a", "llm:gemini-3.8-flash-high", 1)
        assert NLoc.lift_rule_reads(s, now) == 0

    def test_old_ones_are_not_re_read_because_the_call_cap_is_a_money_cap(self):
        s, dbmod, dt = self._s()
        now = self._art(s, dbmod, dt, "a", "rules-v1", 10)
        assert NLoc.lift_rule_reads(s, now) == 0

    def test_an_article_whose_body_was_never_read_is_not_resent(self):
        s, dbmod, dt = self._s()
        now = self._art(s, dbmod, dt, "a", "rules-v1", 1, body=None)
        assert NLoc.lift_rule_reads(s, now) == 0
