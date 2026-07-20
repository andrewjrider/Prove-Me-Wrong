import sqlite3
from datetime import datetime, timedelta

from flask import current_app, g

SCHEMA = """
CREATE TABLE IF NOT EXISTS claims (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'approved',
    summary_agree TEXT,
    summary_disagree TEXT,
    summary_response_count INTEGER NOT NULL DEFAULT 0,
    summary_generated_at TEXT
);

CREATE TABLE IF NOT EXISTS votes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_id INTEGER NOT NULL,
    voter_id TEXT NOT NULL,
    choice TEXT NOT NULL CHECK(choice IN ('agree', 'disagree')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (claim_id) REFERENCES claims(id) ON DELETE CASCADE,
    UNIQUE(claim_id, voter_id)
);

CREATE INDEX IF NOT EXISTS idx_votes_claim ON votes(claim_id, choice);

CREATE TABLE IF NOT EXISTS responses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_id INTEGER NOT NULL,
    voter_id TEXT NOT NULL,
    side TEXT NOT NULL CHECK(side IN ('agree', 'disagree')),
    body TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (claim_id) REFERENCES claims(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_responses_claim ON responses(claim_id, side);

CREATE TABLE IF NOT EXISTS rate_limit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ip_address TEXT NOT NULL,
    action TEXT NOT NULL,
    claim_id INTEGER,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_rate_limit_lookup ON rate_limit_events(ip_address, action, claim_id, created_at);

CREATE TABLE IF NOT EXISTS page_views (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_id INTEGER,
    referrer_host TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_page_views_created ON page_views(created_at);
CREATE INDEX IF NOT EXISTS idx_page_views_claim ON page_views(claim_id, created_at);
"""


def utc_now():
    return datetime.utcnow().replace(microsecond=0).isoformat()


def utc_ago(seconds):
    return (datetime.utcnow() - timedelta(seconds=seconds)).replace(microsecond=0).isoformat()


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE_PATH"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(_error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_app(app):
    app.teardown_appcontext(close_db)


def ensure_column(conn, table, column, definition):
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db():
    conn = get_db()
    conn.executescript(SCHEMA)
    # Existing rows predate the status column; ALTER backfills them with the
    # 'approved' default, so claims that were already live stay live.
    ensure_column(conn, "claims", "status", "TEXT NOT NULL DEFAULT 'approved'")
    ensure_column(conn, "claims", "summary_agree", "TEXT")
    ensure_column(conn, "claims", "summary_disagree", "TEXT")
    ensure_column(conn, "claims", "summary_response_count", "INTEGER NOT NULL DEFAULT 0")
    ensure_column(conn, "claims", "summary_generated_at", "TEXT")
    # Created after ensure_column, since on an existing DB the status column
    # doesn't exist until the ALTER above runs.
    conn.execute("CREATE INDEX IF NOT EXISTS idx_claims_status ON claims(status, created_at)")
    conn.commit()


def row_to_dict(row):
    return dict(row) if row else None


def create_claim(text, status="approved"):
    conn = get_db()
    now = utc_now()
    conn.execute(
        "INSERT INTO claims (text, created_at, status) VALUES (?, ?, ?)",
        (text.strip(), now, status),
    )
    claim_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.commit()
    return claim_id


def get_claim(claim_id):
    row = get_db().execute("SELECT * FROM claims WHERE id = ?", (claim_id,)).fetchone()
    return row_to_dict(row)


def get_claims():
    """Approved claims only — this is the public listing."""
    rows = get_db().execute(
        "SELECT * FROM claims WHERE status = 'approved' ORDER BY created_at DESC, id DESC"
    ).fetchall()
    return [dict(row) for row in rows]


def get_pending_claims():
    rows = get_db().execute(
        "SELECT * FROM claims WHERE status = 'pending' ORDER BY created_at ASC, id ASC"
    ).fetchall()
    return [dict(row) for row in rows]


def get_pending_count():
    return get_db().execute(
        "SELECT COUNT(*) AS c FROM claims WHERE status = 'pending'"
    ).fetchone()["c"]


def set_claim_status(claim_id, status):
    conn = get_db()
    conn.execute("UPDATE claims SET status = ? WHERE id = ?", (status, claim_id))
    conn.commit()


def update_claim_summary(claim_id, agree_summary, disagree_summary, response_count):
    conn = get_db()
    conn.execute(
        """
        UPDATE claims
        SET summary_agree = ?, summary_disagree = ?, summary_response_count = ?, summary_generated_at = ?
        WHERE id = ?
        """,
        (agree_summary, disagree_summary, response_count, utc_now(), claim_id),
    )
    conn.commit()


def get_vote_counts(claim_id):
    rows = get_db().execute(
        "SELECT choice, COUNT(*) AS count FROM votes WHERE claim_id = ? GROUP BY choice",
        (claim_id,),
    ).fetchall()
    counts = {"agree": 0, "disagree": 0}
    for row in rows:
        counts[row["choice"]] = row["count"]
    return counts


def get_voter_choice(claim_id, voter_id):
    row = get_db().execute(
        "SELECT choice FROM votes WHERE claim_id = ? AND voter_id = ?",
        (claim_id, voter_id),
    ).fetchone()
    return row["choice"] if row else None


def cast_vote(claim_id, voter_id, choice):
    conn = get_db()
    now = utc_now()
    conn.execute(
        """
        INSERT INTO votes (claim_id, voter_id, choice, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(claim_id, voter_id)
        DO UPDATE SET choice = excluded.choice, updated_at = excluded.updated_at
        """,
        (claim_id, voter_id, choice, now, now),
    )
    conn.commit()


def add_response(claim_id, voter_id, side, body):
    conn = get_db()
    now = utc_now()
    conn.execute(
        """
        INSERT INTO responses (claim_id, voter_id, side, body, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (claim_id, voter_id, side, body.strip(), now),
    )
    conn.commit()


def get_responses(claim_id):
    rows = get_db().execute(
        "SELECT * FROM responses WHERE claim_id = ? ORDER BY created_at DESC, id DESC",
        (claim_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def count_rate_limit_events(ip_address, action, since, claim_id=None):
    conn = get_db()
    if claim_id is None:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM rate_limit_events WHERE ip_address = ? AND action = ? AND created_at >= ?",
            (ip_address, action, since),
        ).fetchone()
    else:
        row = conn.execute(
            """
            SELECT COUNT(*) AS c FROM rate_limit_events
            WHERE ip_address = ? AND action = ? AND claim_id = ? AND created_at >= ?
            """,
            (ip_address, action, claim_id, since),
        ).fetchone()
    return row["c"]


def record_rate_limit_event(ip_address, action, claim_id=None):
    conn = get_db()
    conn.execute(
        "INSERT INTO rate_limit_events (ip_address, action, claim_id, created_at) VALUES (?, ?, ?, ?)",
        (ip_address, action, claim_id, utc_now()),
    )
    conn.commit()


# --- Analytics (page views) -------------------------------------------------
# Privacy-light: we store a coarse referrer host (never the full URL or query,
# never IP or any PII) and a timestamp. Enough to answer "is anyone here, and
# where are they coming from" without building a profile of anyone.

def record_page_view(claim_id, referrer_host):
    conn = get_db()
    conn.execute(
        "INSERT INTO page_views (claim_id, referrer_host, created_at) VALUES (?, ?, ?)",
        (claim_id, (referrer_host or "")[:255], utc_now()),
    )
    conn.commit()


def count_page_views(since=None):
    if since:
        return get_db().execute(
            "SELECT COUNT(*) AS c FROM page_views WHERE created_at >= ?", (since,)
        ).fetchone()["c"]
    return get_db().execute("SELECT COUNT(*) AS c FROM page_views").fetchone()["c"]


def top_claims_by_views(limit=8, since=None):
    params = []
    where = "WHERE pv.claim_id IS NOT NULL"
    if since:
        where += " AND pv.created_at >= ?"
        params.append(since)
    params.append(limit)
    rows = get_db().execute(
        f"""
        SELECT c.id AS id, c.text AS text, c.status AS status, COUNT(*) AS views
        FROM page_views pv JOIN claims c ON c.id = pv.claim_id
        {where}
        GROUP BY pv.claim_id
        ORDER BY views DESC, c.id DESC
        LIMIT ?
        """,
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def top_referrers(limit=8, since=None, exclude_host=None):
    params = []
    where = "WHERE referrer_host != ''"
    if since:
        where += " AND created_at >= ?"
        params.append(since)
    if exclude_host:
        where += " AND referrer_host != ?"
        params.append(exclude_host)
    params.append(limit)
    rows = get_db().execute(
        f"""
        SELECT referrer_host, COUNT(*) AS views FROM page_views
        {where}
        GROUP BY referrer_host ORDER BY views DESC LIMIT ?
        """,
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def count_votes():
    return get_db().execute("SELECT COUNT(*) AS c FROM votes").fetchone()["c"]


def count_responses():
    return get_db().execute("SELECT COUNT(*) AS c FROM responses").fetchone()["c"]
