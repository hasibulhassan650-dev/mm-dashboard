"""fix_all_sequences must never move an id sequence BACKWARDS.

17-Nov and 20-Nov: the GitHub backup run's resync set a sequence to MAX(id) while the PC's run held uncommitted rows with higher
ids; the next insert then died on 'duplicate key (id)=170544' and a whole pipeline step was lost."""
import db


class _Conn:
    def __init__(self, log, last):
        self.log, self.last = log, last

    def __enter__(self): return self
    def __exit__(self, *a): return False
    def commit(self): pass

    def execute(self, stmt, *a, **k):
        sql = str(stmt)
        self.log.append(sql)

        class R:
            def __init__(s, v): s.v = v
            def scalar(s): return s.v
        if "pg_get_serial_sequence" in sql and sql.startswith("SELECT pg_get"):
            return R("public.t_id_seq")
        if "last_value" in sql:
            return R(self.last)
        return R(None)


class _Engine:
    url = "postgresql://x"

    def __init__(self, log, last): self.log, self.last = log, last
    def connect(self): return _Conn(self.log, self.last)


def test_the_sequence_target_is_never_below_the_sequences_own_position(monkeypatch):
    log = []
    monkeypatch.setattr(db, "get_engine", lambda: _Engine(log, 500))
    db.fix_all_sequences()
    setvals = [q for q in log if "setval" in q]
    assert setvals and all("GREATEST" in q and ", 500," in q for q in setvals), setvals[:1]
