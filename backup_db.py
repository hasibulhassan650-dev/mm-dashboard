"""Daily local backup of every table to the desk PC, never to the (public) repository or a GitHub artifact.

Why: BB-published data can be re-read, but the desk's own entries (desk_marks, buyback marks), the recorded BB absences,
calendar decisions, tenor overrides and the news-derived events cannot. Nothing backed them up, and the repo is PUBLIC, so a
backup artifact on GitHub would be public too. The PC keeps 14 daily copies as gzip CSV plus a manifest (row counts and
SHA-256 of each file), written to a temporary folder and renamed only when complete.

  python backup_db.py                       take today's backup (the full refresh does this once a day)
  python backup_db.py --verify <folder>     check a backup against its manifest
  python backup_db.py --restore <folder> [--tables desk_marks,desk_buyback_marks] [--apply]
                                            insert the rows that are not already there (by id); dry run without --apply

Restore order: run `python -c "import db; db.init_db()"` on an empty database first (it creates the tables), then restore.
For an off-site copy, point MM_BACKUP_DIR at a folder your cloud drive syncs.
"""
import csv
import datetime
import gzip
import hashlib
import json
import logging
import os
import shutil
import sys
from pathlib import Path

log = logging.getLogger("backup")
KEEP_DAYS = 14


def backup_root() -> Path:
    env = os.environ.get("MM_BACKUP_DIR")
    if env:
        return Path(env)
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "mm_dashboard" / "backups"


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run_backup(today: datetime.date = None, root: Path = None, engine=None) -> dict:
    """{'tables', 'rows', 'path', 'skipped'}: skipped = today's backup already exists."""
    from sqlalchemy import text

    import db
    today = today or datetime.date.today()
    root = Path(root) if root else backup_root()
    final = root / today.isoformat()
    if (final / "manifest.json").exists():
        return {"tables": 0, "rows": 0, "path": str(final), "skipped": True}
    engine = engine or db.get_engine()
    tmp = root / (today.isoformat() + ".partial")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    manifest, total = {}, 0
    with engine.connect() as conn:
        for table in db.Base.metadata.sorted_tables:
            cols = [c.name for c in table.columns]
            f = tmp / f"{table.name}.csv.gz"
            n = 0
            with gzip.open(f, "wt", encoding="utf-8", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(cols)
                q = conn.dialect.identifier_preparer.quote
                res = conn.execute(text(f"SELECT {', '.join(q(c) for c in cols)} FROM {q(table.name)}"))
                for row in res:
                    w.writerow(["" if v is None else (v.isoformat() if hasattr(v, "isoformat") else v) for v in row])
                    n += 1
            manifest[table.name] = {"rows": n, "columns": cols, "sha256": _sha(f)}
            total += n
    (tmp / "manifest.json").write_text(json.dumps({"taken_utc": datetime.datetime.utcnow().isoformat(),
                                                   "tables": manifest}, indent=1), encoding="utf-8")
    if final.exists():
        shutil.rmtree(final)
    tmp.rename(final)
    # retention: the newest KEEP_DAYS complete backups
    done = sorted(p for p in root.iterdir() if p.is_dir() and not p.name.endswith(".partial") and (p / "manifest.json").exists())
    for old in done[:-KEEP_DAYS]:
        shutil.rmtree(old, ignore_errors=True)
    log.info("backup: %d tables, %d rows -> %s", len(manifest), total, final)
    return {"tables": len(manifest), "rows": total, "path": str(final), "skipped": False}


def verify(folder) -> list:
    """Problems with a backup folder ([] = every file matches its manifest)."""
    folder = Path(folder)
    problems = []
    try:
        m = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))["tables"]
    except Exception as exc:
        return [f"manifest unreadable: {exc}"]
    for name, info in m.items():
        f = folder / f"{name}.csv.gz"
        if not f.exists():
            problems.append(f"{name}: file missing")
        elif _sha(f) != info["sha256"]:
            problems.append(f"{name}: checksum differs from the manifest")
    return problems


def restore(folder, tables=None, apply: bool = False, engine=None) -> dict:
    """Insert the rows of a backup that are not already present (by primary key). Dry run unless apply=True."""
    from sqlalchemy import text

    import db
    folder = Path(folder)
    bad = verify(folder)
    if bad:
        raise RuntimeError("backup failed verification: " + "; ".join(bad))
    engine = engine or db.get_engine()
    out = {}
    meta = {t.name: t for t in db.Base.metadata.sorted_tables}
    for name in (tables or list(meta)):
        table = meta[name]
        pk = [c.name for c in table.primary_key.columns]
        added = 0
        with gzip.open(folder / f"{name}.csv.gz", "rt", encoding="utf-8", newline="") as fh, engine.begin() as conn:
            for rec in csv.DictReader(fh):
                row = {k: (None if v == "" else v) for k, v in rec.items()}
                q = conn.dialect.identifier_preparer.quote
                where = " AND ".join(f"{q(k)} = :{k}" for k in pk)
                if conn.execute(text(f"SELECT 1 FROM {q(name)} WHERE {where}"), {k: row[k] for k in pk}).first():
                    continue
                added += 1
                if apply:
                    cols = list(row)
                    conn.execute(text(f"INSERT INTO {q(name)} ({', '.join(q(c) for c in cols)}) "
                                      f"VALUES ({', '.join(':' + c for c in cols)})"), row)
        out[name] = added
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    a = sys.argv[1:]
    if a[:1] == ["--verify"]:
        p = verify(a[1])
        print("OK" if not p else "\n".join(p))
        sys.exit(1 if p else 0)
    if a[:1] == ["--restore"]:
        tabs = None
        if "--tables" in a:
            tabs = a[a.index("--tables") + 1].split(",")
        print(restore(a[1], tabs, apply="--apply" in a))
        sys.exit(0)
    print(run_backup())
