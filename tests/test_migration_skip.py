"""init_db must not take table locks for statements that change nothing.

28-Nov-2026: the PC's start-up queued ~100 no-op ALTER/VALIDATE statements behind another session's lock on omo_transactions, hit the
2-minute statement timeout three times (6 minutes) and lost its connection. Asking the catalogue first removes the contention."""
import db


COLS = {("omo_transactions", "rate_range"), ("press_releases", "body_text")}
CONS = {"ck_cm_rate_band": True, "ck_new_band": False}
IDX = {"ux_pys_auction_isin"}


def applied(ddl):
    return db._already_applied(ddl, COLS, CONS, IDX)


def test_a_column_that_exists_is_not_altered_and_one_that_does_not_is():
    assert applied("ALTER TABLE omo_transactions ADD COLUMN IF NOT EXISTS rate_range VARCHAR(30)")
    assert applied("ALTER TABLE press_releases ADD COLUMN body_text TEXT")                 # the SQLite-style variant too
    assert not applied("ALTER TABLE omo_transactions ADD COLUMN IF NOT EXISTS brand_new REAL")


def test_a_validated_constraint_is_not_revalidated_but_an_unvalidated_one_is():
    assert applied("ALTER TABLE call_money_rates ADD CONSTRAINT ck_cm_rate_band CHECK (a <= b) NOT VALID")
    assert applied("ALTER TABLE call_money_rates VALIDATE CONSTRAINT ck_cm_rate_band")
    assert not applied("ALTER TABLE x VALIDATE CONSTRAINT ck_new_band")                    # present but not yet validated: still to do
    assert not applied("ALTER TABLE x ADD CONSTRAINT ck_missing CHECK (a > 0) NOT VALID")


def test_an_existing_index_is_skipped_and_an_unknown_statement_is_never_skipped():
    assert applied("CREATE UNIQUE INDEX IF NOT EXISTS ux_pys_auction_isin ON primary_yield_snapshots (auction_date, isin)")
    assert not applied("CREATE INDEX IF NOT EXISTS ux_other ON t (c)")
    assert not applied("UPDATE something SET a = 1")                                          # anything we cannot classify still runs


def test_sqlite_runs_everything_as_before():
    from sqlalchemy import create_engine
    assert db._postgres_schema_facts(create_engine("sqlite:///:memory:")) is None
