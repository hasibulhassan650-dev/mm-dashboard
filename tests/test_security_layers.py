"""Layered hardening that must not silently regress (the repo is PUBLIC and the desk's marks are not recomputable)."""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _read(p):
    return (ROOT / p).read_text(encoding="utf-8")


def test_scraped_text_cannot_ping_a_user_or_add_a_link_from_a_github_issue():
    """A headline with '@someone' would notify them from the bot; markdown would let a headline plant a link."""
    from engines.announce import plain, _safe_url, issue_title
    t = plain("Hello @octocat [click](https://evil.example) <img src=x onerror=1>")
    assert "@octocat" not in t and "](" not in t and "<img" not in t
    assert _safe_url("javascript:alert(1)") == "(link withheld)" and _safe_url("https://x.example/a") == "https://x.example/a"
    assert "@x" not in issue_title({"prefix": "BB", "title": "ping @x now", "serial": "pr1"})


def test_a_secret_shape_is_redacted_before_it_can_reach_a_public_issue():
    from engines.step_ledger import redact
    for s in ("postgresql://u:pw@h/db", "ghp_" + "a" * 30, "AIza" + "b" * 30, "sk-" + "c" * 30, "eyJabcdefghij.eyJklmnopqrst.sig"):
        assert redact(f"error with {s} inside") == "error with [redacted] inside", s


def test_secrets_are_compared_in_constant_time_and_headers_are_set():
    a = _read("frontend/lib/auth.ts")
    assert "safeEqual" in a and "=== sig" not in a and "=== PASSWORD_SHA256" not in a and "input === env" not in a
    n = _read("frontend/next.config.ts")
    for h in ("X-Frame-Options", "frame-ancestors 'none'", "X-Content-Type-Options", "Strict-Transport-Security"):
        assert h in n, h


def test_external_links_taken_from_stored_data_go_through_safeHref():
    """A poisoned source row must never become a clickable javascript: link."""
    for p in ("frontend/app/liquidity-watch/page.tsx", "frontend/app/press/page.tsx", "frontend/components/SukukPanel.tsx",
              "frontend/app/drilldown/page.tsx", "frontend/app/fx/page.tsx"):
        src = _read(p)
        raw = re.findall(r'<a [^>]*href=\{(?!safeHref)([^}]*)\}', src)
        assert not [r for r in raw if "http" not in r], (p, raw)


def test_workflow_inputs_never_reach_a_shell_as_script_text():
    """A workflow_dispatch input spliced into a `run:` script is a command-injection point; it must travel through env: instead."""
    import yaml
    for p in (ROOT / ".github" / "workflows").glob("*.yml"):
        doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        for jname, job in (doc.get("jobs") or {}).items():
            for step in job.get("steps") or []:
                run = step.get("run") or ""
                assert "${{ inputs." not in run and "${{ github.event.inputs" not in run, f"{p.name}/{jname}: {step.get('name')}"


def test_the_api_does_not_echo_driver_errors_and_bounds_its_pages():
    d = _read("api/routers/desk.py")
    assert "could not store the mark: {exc}" not in d and "could not store the buyback: {exc}" not in d
    assert "le=500" in _read("api/routers/meta.py") and "le=5000" in _read("api/routers/securities.py")
