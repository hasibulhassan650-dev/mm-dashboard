"""How the Doctor's repairs read on the Guardian page: a sentence, with the proof and how to undo it.

Pure text (api/ deploys rooted at api/ and cannot import engines/; the Doctor stores its repairs in data_changes as JSON).
"""


def doctor_sentence(change_class: str, detail: str, change_id) -> str:
    """The Doctor (engines/doctor.py) stores its repairs as JSON with the proof and the OLD values; the page shows a sentence.
    Anything that is not a Doctor record is passed through unchanged."""
    if change_class not in ("tenor_autoheal", "tenor_autoheal_reverted", "lost_day"):
        return detail
    try:
        import json
        d = json.loads(detail)
    except Exception:
        return detail
    if change_class == "lost_day":
        return f"{d.get('dataset')} {d.get('day')} recorded as lost for good. {d.get('evidence', '')}"
    p = d.get("proof") or {}
    s = (f"{d.get('instrument')} {d.get('accepted_crore'):,.2f} cr dealt {d.get('deal_date')}: tenor {d.get('old_tenor_days')} -> "
         f"{d.get('new_tenor_days')} days, matures {d.get('new_maturity')} (was {d.get('old_maturity')}). Proof: BB printed "
         f"{p.get('printed_crore')} cr, we held {p.get('held_crore')}, and {p.get('rate_pct')}% for {d.get('new_tenor_days')} days "
         f"reproduces {p.get('reproduced_crore')}.")
    if change_class == "tenor_autoheal":
        s += f" To undo: engines.doctor.revert_tenor_autoheal(session, {change_id})."
    else:
        s = "REVERTED: " + s
    return s
