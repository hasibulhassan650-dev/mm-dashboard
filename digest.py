"""
digest.py — the daily "here is what the data actually did" report.

Every alarm in this project is exception-based: it fires when something breaks.
That leaves one hole nothing else can cover — if the alarm itself stops, silence
looks exactly like health. It looked like health for five days in Sep-2026.

So this runs every morning and reports unconditionally: what ran, how fresh each
series is, what the checks say RIGHT NOW (computed live, not read from the last
run's stored verdict), what the self-heal repaired, and which CI jobs failed.
Because it arrives daily, its ABSENCE is itself the alarm.

Writes digest.md (the workflow posts it as an issue comment) and prints it.
Exit code is always 0: a digest that fails to post is not an outage.
"""
import datetime
import json
import os
import subprocess

from sqlalchemy import text

from db import get_session
from validate import integrity_check

RUN_STALE_HOURS = 14
_SERIES = [
    ("Treasury yields", "primary_yield_snapshots", "auction_date"),
    ("OMO operations", "omo_transactions", "transaction_date"),
    ("Call money", "call_money_rates", "trade_date"),
    ("Reference rates", "ref_rates", "trade_date"),
    ("FX auctions", "fx_auction_results", "auction_date"),
    ("Interbank FX", "interbank_fx", "trade_date"),
    ("Exchange rates", "fx_rates_daily", "rate_date"),
    ("Interbank repo", "interbank_repo", "trade_date"),
    ("Secondary (GSOM)", "mtm_snapshots", "settlement_date"),
    ("Cash flows", "daily_net_flow", "flow_date"),
]


def _hours_since(ts):
    if ts is None:
        return float("inf")
    if isinstance(ts, str):
        ts = datetime.datetime.fromisoformat(ts[:19])
    return (datetime.datetime.utcnow() - ts).total_seconds() / 3600.0


def _ci_failures(hours=26):
    """Workflow runs that failed in the last day (best effort — needs gh)."""
    try:
        out = subprocess.run(
            ["gh", "run", "list", "--limit", "60", "--json",
             "name,conclusion,createdAt,databaseId"],
            capture_output=True, text=True, timeout=60).stdout
        runs = json.loads(out or "[]")
    except Exception:
        return None
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(hours=hours)
    out = {}
    for r in runs:
        try:
            when = datetime.datetime.fromisoformat(r["createdAt"].replace("Z", ""))
        except Exception:
            continue
        if when < cutoff:
            continue
        ok, bad = out.get(r["name"], (0, 0))
        if r.get("conclusion") == "success":
            ok += 1
        elif r.get("conclusion"):
            bad += 1
        out[r["name"]] = (ok, bad)
    return out


def build() -> tuple:
    s = get_session()
    lines, problems = [], []
    try:
        row = s.execute(text("SELECT run_utc, errors, quality FROM pipeline_runs "
                             "ORDER BY run_utc DESC LIMIT 1")).fetchone()
        last_run, errors, quality = (row[0], row[1], row[2]) if row else (None, None, None)
        age = _hours_since(last_run)

        if age > RUN_STALE_HOURS:
            problems.append(f"the refresh has not completed for {age:.0f}h")
            lines.append(f"### ❌ Refresh has NOT run for {age:.0f} hours\n"
                         f"Last completed run: `{last_run}`. Every figure on the site is at least "
                         f"that old — check the Actions tab.\n")
        else:
            lines.append(f"### ✅ Refresh ran {age:.1f}h ago (`{last_run}`)\n")

        # ── live integrity, not the stored verdict ────────────────────────────
        rep = integrity_check()
        if rep.get("ok"):
            lines.append("**Our data:** all checks pass.\n")
        else:
            problems.append(f"{rep.get('issue_count')} integrity issue(s)")
            by = ", ".join(f"`{k}`={v}" for k, v in sorted(rep.get("by_table", {}).items(),
                                                           key=lambda x: -x[1]))
            issues = rep.get("issues", [])
            # ESCALATIONS LEAD. For eleven days this report opened with an
            # all-clear while a whole feed was dead, because the one finding
            # that mattered was filed where nothing read it. Anything escalated
            # now goes ABOVE the summary line, in full, and is never cut by the
            # [:10] slice -- the worst finding is the one a cap would drop.
            esc = [i for i in issues
                   if "ESCALATED" in i or i.startswith("guardian:")
                   or i.startswith("empty-fetch:")]
            rest = [i for i in issues if i not in esc]
            if esc:
                lines.append(f"### 🚨 {len(esc)} ESCALATION(S) — act on these now\n")
                for i in esc:
                    lines.append(f"- **{i}**")
                lines.append("")
            lines.append(f"**Our data: {rep.get('issue_count')} defect(s)** — {by}\n")
            for i in rest[:10]:
                lines.append(f"- {i}")
            if len(rest) > 10:
                lines.append(f"- _…and {len(rest) - 10} more; "
                             f"`python audit.py` lists every one_")
            lines.append("")

        # Waiting on BB is reported every single day but is NOT counted as a
        # problem and does NOT colour the heading: it is not our data and not
        # ours to fix, and a red heading every day teaches you to stop reading.
        # The ledger supplies the age, which is what separates "BB is late"
        # from "BB has stopped publishing".
        try:
            from engines.guardian import record, open_issues, fingerprint as _fp
            record(s, rep)
            s.commit()
            ages = {i["fingerprint"]: i for i in open_issues(s)}
        except Exception as exc:
            ages, _fp = {}, (lambda x: x)
            lines.append(f"_(issue ledger unavailable: {exc})_\n")
        # BB changed the shape of a page and the parse still came out clean.
        # Above "waiting on BB" because somebody has to look at it, and below
        # the defects because nothing is wrong yet.
        if rep.get("notices"):
            # The notice bucket carries two different meanings, and one heading
            # for both mislabelled them: a gap in what we COVER is not BB
            # restructuring a page.
            shape = [n for n in rep["notices"] if n.startswith("source:")]
            other = [n for n in rep["notices"] if not n.startswith("source:")]

            def _aged(n):
                a = ages.get(_fp(n))
                return f" _(since {a['age_days']}d ago)_" if a and a.get("age_days") else ""

            if shape:
                lines.append(f"**BB changed a page: {len(shape)}** — the parse is still "
                             f"clean because columns are read by header text, not position; "
                             f"confirm the new layout means what we think it means\n")
                for nt in shape[:10]:
                    lines.append(f"- {nt}{_aged(nt)}")
                lines.append("")
            if other:
                lines.append(f"**Worth knowing: {len(other)}** — not a defect in the data "
                             f"we hold, and not BB being late\n")
                for nt in other[:10]:
                    lines.append(f"- {nt}{_aged(nt)}")
                lines.append("")

        # Days BB itself does not publish, each one established by re-reading a
        # COMPLETE month of BB's own page. The gap check passes over them so one
        # permanent hole does not sit red for ever — which means this line is
        # the only thing standing between "known hole" and "invisible".
        try:
            from engines.absences import summary as _absence_summary
            _abs = _absence_summary(s)
        except Exception:
            _abs = {}
        if _abs:
            parts = ", ".join(f"`{k}` {v['count']} (latest {v['latest']})"
                              for k, v in sorted(_abs.items()))
            lines.append(f"**Known holes at BB: {sum(v['count'] for v in _abs.values())}** — "
                         f"{parts}. Confirmed absent by re-reading BB's own page from the "
                         f"start of the month, so the gap check passes over them; they are "
                         f"holes in the record, not in our fetching.\n")

        # Holes that are OURS: a day we had and destroyed, which BB cannot give
        # back. Printed apart from the BB line above because telling the desk a
        # hole is BB's when it is ours is the one thing this report must not do.
        try:
            from engines.absences import summary as _abs_summary
            _lost = _abs_summary(s, kind="lost")
        except Exception:
            _lost = {}
        if _lost:
            parts = ", ".join(f"`{k}` {v['count']} (latest {v['latest']})"
                              for k, v in sorted(_lost.items()))
            lines.append(f"**Unrecoverable holes in OUR record: "
                         f"{sum(v['count'] for v in _lost.values())}** — {parts}. We had "
                         f"these and lost them; BB cannot re-serve them. Named here so "
                         f"they stay visible, not hidden as BB's omission.\n")

        if rep.get("waiting"):
            lines.append(f"**Waiting on Bangladesh Bank: {rep.get('waiting_count')}** — "
                         f"not our data and not actionable, listed so it is never forgotten\n")
            for w in rep["waiting"][:10]:
                a = ages.get(_fp(w))
                age = f" _(waiting {a['age_days']}d)_" if a and a.get("age_days") else ""
                lines.append(f"- {w}{age}")
            lines.append("")

        # ── what the self-heal fixed on the last run ─────────────────────────
        try:
            q = json.loads(quality) if quality else {}
        except Exception:
            q = {}
        if q.get("repaired"):
            fixed = ", ".join(f"{k}={v}" for k, v in q["repaired"].items())
            lines.append(f"**Self-heal (last run):** repaired {fixed}.\n")
        if q.get("repair_review"):
            problems.append("rows the self-heal could not resolve")
            lines.append(f"**Needs a human ({len(q['repair_review'])}):**")
            for x in q["repair_review"][:5]:
                lines.append(f"- {x}")
            lines.append("")

        if errors:
            try:
                errs = json.loads(errors)
            except Exception:
                errs = [str(errors)]
            if errs:
                problems.append(f"{len(errs)} step error(s) in the last run")
                lines.append(f"**Last run reported {len(errs)} step error(s):** "
                             + "; ".join(str(e)[:100] for e in errs[:4]) + "\n")

        # ── freshness table ──────────────────────────────────────────────────
        lines.append("| Series | Rows | Latest data | Last write |")
        lines.append("|---|---:|---|---|")
        for label, table, datecol in _SERIES:
            try:
                n = s.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()          # noqa: S608
                latest = s.execute(text(f"SELECT MAX({datecol}) FROM {table}")).scalar()  # noqa: S608
                ing = s.execute(text(f"SELECT MAX(ingested_utc) FROM {table}")).scalar() \
                    if table != "daily_net_flow" else \
                    s.execute(text("SELECT MAX(computed_utc) FROM daily_net_flow")).scalar()
                ing_age = _hours_since(ing)
                ing_txt = "never" if ing_age == float("inf") else f"{ing_age:.0f}h ago"
                lines.append(f"| {label} | {n:,} | {latest} | {ing_txt} |")
            except Exception as exc:
                lines.append(f"| {label} | ? | ? | error: {str(exc)[:40]} |")
        lines.append("")

        ci = _ci_failures()
        if ci:
            bad = {k: v for k, v in ci.items() if v[1]}
            if bad:
                problems.append("failing CI jobs")
                lines.append("**CI failures in the last 24h:** "
                             + ", ".join(f"{k} ({v[1]} failed)" for k, v in bad.items()) + "\n")
            else:
                lines.append("**CI:** every workflow run in the last 24h succeeded.\n")
    finally:
        s.close()

    head = ("## 🟢 Daily data digest — all clear" if not problems
            else "## 🔴 Daily data digest — " + "; ".join(problems))
    stamp = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    body = head + f"\n_{stamp}_\n\n" + "\n".join(lines)
    body += ("\n---\n_This arrives every morning. **If it stops arriving, that is itself the alarm** — "
             "the digest workflow or the runner is down._")
    return body, problems


if __name__ == "__main__":
    body, problems = build()
    with open("digest.md", "w", encoding="utf-8") as f:
        f.write(body)
    # Echo for the job log, but never let a console encoding (Windows cp1252
    # cannot represent the status emoji) turn a report into a failed step.
    try:
        print(body)
    except UnicodeEncodeError:
        print(body.encode("ascii", "replace").decode("ascii"))
    # Always exit 0: a digest is a report, not a gate. The integrity gate and the
    # sentinel are what fail builds; this must never mask them by failing first.
    raise SystemExit(0)
