"""Persistance des runs de test dans une base SQLite.

Deux tables :
  runs         -> une ligne par execution (le resume + les metriques QoS)
  test_results -> une ligne par test de chaque run (le detail)

Le fichier de base vit dans data/runs.db a cote du code. Il est volontairement
exclu de Git (.gitignore) : sinon chaque deploiement GitHub Actions ecraserait
l'historique accumule en production.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager

DEFAULT_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "runs.db")
DB_PATH = os.environ.get("RUNS_DB_PATH", DEFAULT_DB_PATH)

# Retention : au-dela, on purge les plus vieux runs (le disque PythonAnywhere
# gratuit est limite a 512 Mo).
MAX_RUNS_KEPT = 500

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT    NOT NULL,
    api             TEXT    NOT NULL,
    base_url        TEXT    NOT NULL,
    triggered_by    TEXT    NOT NULL DEFAULT 'web',
    total           INTEGER NOT NULL,
    passed          INTEGER NOT NULL,
    failed          INTEGER NOT NULL,
    errors          INTEGER NOT NULL,
    error_rate      REAL    NOT NULL,
    availability    REAL    NOT NULL,
    latency_ms_avg  REAL    NOT NULL,
    latency_ms_p95  REAL    NOT NULL,
    latency_ms_max  REAL    NOT NULL,
    http_calls      INTEGER NOT NULL,
    duration_ms     REAL    NOT NULL,
    verdict         TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS test_results (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    position    INTEGER NOT NULL,
    name        TEXT    NOT NULL,
    category    TEXT    NOT NULL,
    description TEXT    NOT NULL,
    status      TEXT    NOT NULL,
    latency_ms  REAL    NOT NULL DEFAULT 0,
    details     TEXT    NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_runs_timestamp ON runs(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_results_run ON test_results(run_id, position);
"""


@contextmanager
def connect():
    """Connexion SQLite avec lignes accessibles par nom de colonne."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    """Cree les tables si besoin. Idempotent, appele au demarrage de l'app."""
    with connect() as conn:
        conn.executescript(SCHEMA)


def save_run(run: dict, triggered_by: str = "web") -> int:
    """Enregistre un run complet (resume + detail des tests). Retourne son id."""
    summary = run["summary"]
    with connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO runs (
                timestamp, api, base_url, triggered_by,
                total, passed, failed, errors,
                error_rate, availability,
                latency_ms_avg, latency_ms_p95, latency_ms_max,
                http_calls, duration_ms, verdict
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                run["timestamp"],
                run["api"],
                run["base_url"],
                triggered_by,
                summary["total"],
                summary["passed"],
                summary["failed"],
                summary["errors"],
                summary["error_rate"],
                summary["availability"],
                summary["latency_ms_avg"],
                summary["latency_ms_p95"],
                summary["latency_ms_max"],
                summary["http_calls"],
                summary["duration_ms"],
                summary["verdict"],
            ),
        )
        run_id = int(cursor.lastrowid)

        conn.executemany(
            """
            INSERT INTO test_results
                (run_id, position, name, category, description, status, latency_ms, details)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            [
                (
                    run_id,
                    position,
                    t["name"],
                    t["category"],
                    t["description"],
                    t["status"],
                    t.get("latency_ms") or 0,
                    t.get("details") or "",
                )
                for position, t in enumerate(run["tests"])
            ],
        )
        _purge(conn)
    return run_id


def _purge(conn: sqlite3.Connection) -> None:
    """Conserve uniquement les MAX_RUNS_KEPT runs les plus recents."""
    conn.execute(
        """
        DELETE FROM test_results WHERE run_id IN (
            SELECT id FROM runs ORDER BY id DESC LIMIT -1 OFFSET ?
        )
        """,
        (MAX_RUNS_KEPT,),
    )
    conn.execute(
        "DELETE FROM runs WHERE id IN (SELECT id FROM runs ORDER BY id DESC LIMIT -1 OFFSET ?)",
        (MAX_RUNS_KEPT,),
    )


def list_runs(limit: int = 30) -> list[dict]:
    """Les `limit` derniers runs, du plus recent au plus ancien (resume seul)."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(row) for row in rows]


def get_run(run_id: int) -> dict | None:
    """Un run complet (resume + tests), ou None s'il n'existe pas."""
    with connect() as conn:
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        tests = conn.execute(
            "SELECT * FROM test_results WHERE run_id = ? ORDER BY position", (run_id,)
        ).fetchall()
    run = dict(row)
    run["tests"] = [dict(t) for t in tests]
    return run


def last_run() -> dict | None:
    """Le run le plus recent, avec le detail de ses tests."""
    with connect() as conn:
        row = conn.execute("SELECT id FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    return get_run(int(row["id"])) if row else None


def trend(limit: int = 30) -> list[dict]:
    """Serie chronologique (ancien -> recent) pour tracer les courbes."""
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT id, timestamp, latency_ms_avg, latency_ms_p95, error_rate, verdict
            FROM runs ORDER BY id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in reversed(rows)]


def global_stats() -> dict:
    """Agregats sur tout l'historique conserve (vue 'sante globale')."""
    with connect() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*)                AS runs_count,
                   AVG(availability)       AS availability_avg,
                   AVG(error_rate)         AS error_rate_avg,
                   AVG(latency_ms_avg)     AS latency_avg,
                   MAX(latency_ms_p95)     AS latency_p95_max,
                   MIN(timestamp)          AS first_run,
                   MAX(timestamp)          AS last_run
            FROM runs
            """
        ).fetchone()
    stats = dict(row)
    for key in ("availability_avg", "error_rate_avg", "latency_avg", "latency_p95_max"):
        stats[key] = round(stats[key], 4) if stats[key] is not None else 0.0
    return stats
