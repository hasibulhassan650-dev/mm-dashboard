"""What KIND of failure is this? One pure, table-driven answer, so nothing reads an error text by eye again.

The interbank-repo incident (12-16 Nov 2026): a CheckViolation was recorded 32 times in pipeline_runs.errors and nothing read
it; a blocked GitHub run that logged "OK (fetched 0)" then overwrote it as the "latest row", and the freshness rule said "our
fetch reports success". A person and an agent read logs to find the cause. The cause was a CLASS of failure the code already
knew how to handle (a BB row that violates a database rule), so the first job of a self-repairing system is to name the class
in code, from the evidence, and to say who owns it and what may be done.

Used by weekly_fetch.record_step_runs (written beside every step's outcome), validate (the diagnosis in an escalation) and the
Doctor (which picks the playbook). Pure functions of text: no database, no network.
"""
import re
from typing import Dict, NamedTuple, Optional

DB_CONSTRAINT = "DB_CONSTRAINT"      # BB printed a row the database refuses (a CHECK / NOT NULL / UNIQUE rule)
BB_BLOCKED = "BB_BLOCKED"            # a challenge / block page where a table should be
BB_DOWN = "BB_DOWN"                  # BB's site under maintenance or serving server errors
PDF_UNSERVED = "PDF_UNSERVED"        # a listed PDF answers "Request Rejected" (identical to a file that does not exist)
PARSE_DEGRADED = "PARSE_DEGRADED"    # the page's layout moved and the parse got worse
EMPTY = "EMPTY"                      # the fetch ran and returned nothing; blocked vs nothing-published is not yet known
NETWORK = "NETWORK"                  # timeouts, resets, DNS: the environment, not BB's content
UNKNOWN = "UNKNOWN"


class Policy(NamedTuple):
    owner: str            # bb | us | environment | unknown
    retryable: bool       # will the same call plausibly work later without a change?
    grace_days: int       # working days to wait before this is OUR escalation (0 = at once)
    playbook: Optional[str]   # the Doctor's repair for this class, or None (diagnose and escalate only)


CLASS_POLICY: Dict[str, Policy] = {
    DB_CONSTRAINT: Policy("bb", False, 0, "isolate_and_blank"),
    BB_BLOCKED: Policy("environment", True, 2, None),
    BB_DOWN: Policy("bb", True, 4, None),
    PDF_UNSERVED: Policy("bb", True, 5, "re_resolve_pdf"),
    PARSE_DEGRADED: Policy("bb", False, 0, None),
    EMPTY: Policy("unknown", True, 2, None),
    NETWORK: Policy("environment", True, 1, None),
    UNKNOWN: Policy("unknown", False, 0, None),
}

# What the OWNER should do about a class that is still failing, in the words the integrity issue shows. A class BB owns is
# not the owner's to fix, and saying so is the point: an issue that keeps asking for action nobody can take gets ignored.
# Every class in CLASS_POLICY must have an entry (tests/test_doctor_open_failures.py).
OWNER_ACTION: Dict[str, str] = {
    DB_CONSTRAINT: ("nothing: BB side (the Doctor blanks the field the database refused). If this is still failing a day "
                    "later, the writer is not isolating the row: paste this issue into Claude Code."),
    BB_DOWN: "nothing: BB side (BB's site is under maintenance or serving errors; the next read after it recovers fixes it).",
    PDF_UNSERVED: "nothing: BB side (BB lists a PDF it does not serve; the Doctor records it and retires the record when BB publishes).",
    BB_BLOCKED: "nothing unless the PC refresh also fails (BB's firewall CAPTCHAs GitHub's runners as a matter of course).",
    NETWORK: "nothing unless the PC refresh also fails (a timeout or reset between the machine and BB).",
    EMPTY: ("nothing yet: BB may have published nothing. If it persists past 2 working days, paste this issue into Claude "
            "Code to tell a block from a real gap."),
    PARSE_DEGRADED: ("BB changed a page layout and our parse got worse, so this step is not being updated correctly: paste "
                     "this issue into Claude Code to fix the parser."),
    UNKNOWN: ("a failure nobody has classified yet: paste this issue into Claude Code and ask it to read the step's last "
              "error in step_runs and teach engines/failures.py the class."),
}

# Order matters: the first rule that matches decides. Each pattern is a fact that appeared in a real error.
_RULES = (
    (DB_CONSTRAINT, re.compile(r"CheckViolation|violates check constraint|IntegrityError|UniqueViolation|NotNullViolation|"
                               r"CHECK constraint failed|NOT NULL constraint failed|rejected by a database rule", re.I)),
    # "not a PDF": the download answered with HTML (a challenge or "Request Rejected" page) where a PDF should be
    (PDF_UNSERVED, re.compile(r"Request Rejected|pdf[^.]{0,40}(not served|download failed)|download failed \(RuntimeError\)|"
                              r"not a PDF", re.I)),
    (BB_DOWN, re.compile(r"under maintenance|maintenance page|HTTP 5\d\d|\b50[0-4]\b|Service Unavailable|Bad Gateway", re.I)),
    # our own "no rows were read ... (blocked, or the page changed)" is AMBIGUOUS by its own words: EMPTY, not BB_BLOCKED
    (EMPTY, re.compile(r"no rows were read|returned no rows|returned nothing|unparseable", re.I)),
    (BB_BLOCKED, re.compile(r"challenge page|block page|F5 CAPTCHA|captcha|enable JavaScript|support ID|human visitor|"
                            r"blocked|firewall|page was not served|not served \(challenge|challenge or no table", re.I)),
    # a parser that crashes on a page it did not expect ('NoneType' object has no attribute 'find_all'): the page is not what
    # it was (a challenge page, or a changed layout) - never a silent success
    (PARSE_DEGRADED, re.compile(r"page changed|parse got worse|less than half the|parsed 0 rows where|layout|degraded|"
                                r"NoneType' object has no attribute", re.I)),
    (EMPTY, re.compile(r"no rows were read|parsed 0 rows|fetched 0\b|returned no rows|no rows", re.I)),
    (NETWORK, re.compile(r"timed? ?out|ReadTimeout|ConnectTimeout|ConnectionError|Connection reset|Connection refused|"
                         r"Name or service not known|getaddrinfo|Max retries exceeded|SSLError", re.I)),
)


def classify(text: Optional[str], step: str = "") -> str:
    """The class of failure an error text describes. `step` is accepted for symmetry and future per-step rules; today the
    text decides. An empty text is not a failure and has no class (UNKNOWN is for text nobody has taught us yet)."""
    t = (text or "").strip()
    if not t:
        return UNKNOWN
    for cls, rx in _RULES:
        if rx.search(t):
            return cls
    return UNKNOWN


def policy(cls: str) -> Policy:
    return CLASS_POLICY.get(cls, CLASS_POLICY[UNKNOWN])


_DIAGNOSIS_STEP = re.compile(r"last failure of ([A-Za-z0-9_\-]+):")


def diagnosis_step(text: Optional[str]) -> Optional[str]:
    """The step a validate line's `diagnosis()` names, or None when the line carries no diagnosis. Kept beside diagnosis() so
    the wording it parses cannot drift from the wording it writes (a test round-trips them)."""
    m = _DIAGNOSIS_STEP.search(text or "")
    return m.group(1) if m else None


def diagnosis(step: str, cls: str, machine: Optional[str], when, error: Optional[str]) -> str:
    """One line a person can act on: what failed, which class, who owns it, and the first words of the error."""
    p = policy(cls)
    who = {"bb": "BB's side", "us": "our side", "environment": "the network/runner", "unknown": "cause not yet known"}[p.owner]
    err = re.sub(r"\s+", " ", (error or "")).strip()
    # a CheckViolation's useful part (the constraint and the failing row) is deep in the message: keep the tail, not the head
    if cls == DB_CONSTRAINT:
        m = re.search(r'(violates check constraint "[^"]+"|CHECK constraint failed: \S+|Failing row contains \([^)]{0,90})', err)
        err = m.group(1) if m else err
    return (f"last failure of {step}: {cls} ({who}) on {machine or '?'} at {str(when)[:16]} - {err[:170]}")
