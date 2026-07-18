import sqlite3
from datetime import datetime

from flask import current_app, g

SCHEMA = """
CREATE TABLE IF NOT EXISTS claims (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL
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
"""


def utc_now():
    return datetime.utcnow().replace(microsecond=0).isoformat()


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


def init_db():
    conn = get_db()
    conn.executescript(SCHEMA)
    conn.commit()


def row_to_dict(row):
    return dict(row) if row else None


def create_claim(text):
    conn = get_db()
    now = utc_now()
    conn.execute(
        "INSERT INTO claims (text, created_at) VALUES (?, ?)",
        (text.strip(), now),
    )
    claim_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.commit()
    return claim_id


def get_claim(claim_id):
    row = get_db().execute("SELECT * FROM claims WHERE id = ?", (claim_id,)).fetchone()
    return row_to_dict(row)


def get_claims():
    rows = get_db().execute("SELECT * FROM claims ORDER BY created_at DESC, id DESC").fetchall()
    return [dict(row) for row in rows]


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
