"""SQLite storage layer for vocabulary entries and hit-tracking statistics.

Provides :class:`VocabDB` — a thin wrapper around a SQLite database with two
tables (``vocab_entry`` for correction pairs, ``vocab_stats`` for per-context
four-dimension hit counts).
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

# Metric name constants — use these instead of raw strings.
METRIC_ASR_MISS = "asr_miss"
METRIC_ASR_HIT = "asr_hit"
METRIC_LLM_HIT = "llm_hit"
METRIC_LLM_MISS = "llm_miss"

# Context key prefix constants.
CTX_ASR = "asr"
CTX_LLM = "llm"
CTX_APP = "app"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def build_context_keys(
    *,
    model_prefix: str = "",
    model_name: str = "",
    app_bundle_id: str = "",
) -> list[str]:
    """Build context bucket keys from model name and app bundle id."""
    keys: list[str] = []
    if model_prefix and model_name:
        keys.append(f"{model_prefix}:{model_name}")
    if app_bundle_id:
        keys.append(f"{CTX_APP}:{app_bundle_id}")
    return keys


# Shared case-insensitive WHERE fragment for (variant, term) lookups.
_WHERE_CI = "variant = ? COLLATE NOCASE AND term = ? COLLATE NOCASE"


class VocabDB:
    """SQLite-backed vocabulary storage with four-dimension hit tracking.

    Parameters
    ----------
    path : str
        File path for the SQLite database.  Use ``":memory:"`` for tests.
    """

    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._ensure_tables()

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _ensure_tables(self) -> None:
        with self._conn:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS vocab_entry (
                    id            INTEGER PRIMARY KEY,
                    term          TEXT NOT NULL,
                    variant       TEXT NOT NULL,
                    source        TEXT NOT NULL DEFAULT 'asr',
                    frequency     INTEGER DEFAULT 1,
                    first_seen    TEXT NOT NULL,
                    last_updated  TEXT NOT NULL,
                    app_bundle_id TEXT DEFAULT '',
                    asr_model     TEXT DEFAULT '',
                    llm_model     TEXT DEFAULT '',
                    enhance_mode  TEXT DEFAULT '',
                    UNIQUE(term COLLATE NOCASE, variant COLLATE NOCASE)
                );

                CREATE TABLE IF NOT EXISTS vocab_stats (
                    entry_id    INTEGER NOT NULL REFERENCES vocab_entry(id) ON DELETE CASCADE,
                    metric      TEXT NOT NULL,
                    context_key TEXT NOT NULL,
                    count       INTEGER DEFAULT 0,
                    last_time   TEXT DEFAULT '',
                    PRIMARY KEY (entry_id, metric, context_key)
                );

                CREATE INDEX IF NOT EXISTS idx_stats_rank
                    ON vocab_stats(metric, context_key, count DESC);
            """)

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def add(
        self,
        variant: str,
        term: str,
        source: str = "asr",
        *,
        app_bundle_id: str = "",
        asr_model: str = "",
        llm_model: str = "",
        enhance_mode: str = "",
    ) -> dict:
        """Insert a new entry or increment frequency if it already exists.

        Returns a dict with the entry's row data, or None on concurrent delete.
        """
        now = _now_iso()
        with self._lock, self._conn:
            try:
                self._conn.execute(
                    """INSERT INTO vocab_entry
                       (term, variant, source, frequency, first_seen, last_updated,
                        app_bundle_id, asr_model, llm_model, enhance_mode)
                       VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?)""",
                    (term, variant, source, now, now,
                     app_bundle_id, asr_model, llm_model, enhance_mode),
                )
            except sqlite3.IntegrityError:
                sets = ["frequency = frequency + 1", "last_updated = ?"]
                params: list = [now]
                if app_bundle_id:
                    sets.append("app_bundle_id = ?")
                    params.append(app_bundle_id)
                if asr_model:
                    sets.append("asr_model = ?")
                    params.append(asr_model)
                if llm_model:
                    sets.append("llm_model = ?")
                    params.append(llm_model)
                if enhance_mode:
                    sets.append("enhance_mode = ?")
                    params.append(enhance_mode)
                params.extend([variant, term])
                self._conn.execute(
                    f"UPDATE vocab_entry SET {', '.join(sets)} WHERE {_WHERE_CI}",
                    params,
                )
            row = self._conn.execute(
                f"SELECT * FROM vocab_entry WHERE {_WHERE_CI}",
                (variant, term),
            ).fetchone()
        return dict(row) if row else None  # type: ignore[return-value]

    def remove(self, variant: str, term: str) -> bool:
        """Delete an entry.  CASCADE removes associated stats.  Returns True if existed."""
        with self._lock, self._conn:
            cur = self._conn.execute(
                f"DELETE FROM vocab_entry WHERE {_WHERE_CI}", (variant, term),
            )
        return cur.rowcount > 0

    def remove_batch(self, pairs: list[tuple[str, str]]) -> int:
        """Delete multiple entries.  Returns count actually removed."""
        count = 0
        with self._lock, self._conn:
            for variant, term in pairs:
                cur = self._conn.execute(
                    f"DELETE FROM vocab_entry WHERE {_WHERE_CI}", (variant, term),
                )
                count += cur.rowcount
        return count

    def get(self, variant: str, term: str) -> dict | None:
        """Return a single entry as dict, or None.  Lookup is case-insensitive."""
        with self._lock:
            row = self._conn.execute(
                f"SELECT * FROM vocab_entry WHERE {_WHERE_CI}", (variant, term),
            ).fetchone()
        return dict(row) if row else None

    def contains(self, variant: str, term: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                f"SELECT 1 FROM vocab_entry WHERE {_WHERE_CI} LIMIT 1",
                (variant, term),
            ).fetchone()
        return row is not None

    def get_all(self) -> list[dict]:
        """Return all entries as a list of dicts."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM vocab_entry ORDER BY last_updated DESC",
            ).fetchall()
        return [dict(r) for r in rows]

    _UPDATABLE_FIELDS = frozenset({
        "source", "app_bundle_id", "asr_model", "llm_model", "enhance_mode",
        "frequency", "first_seen", "last_updated",
    })

    def update_fields(self, entry_id: int, fields: dict) -> None:
        """Update specific fields on an entry by id.

        Only fields in ``_UPDATABLE_FIELDS`` are allowed.
        """
        safe = {k: v for k, v in fields.items() if k in self._UPDATABLE_FIELDS}
        if not safe:
            return
        sets = [f"{k} = ?" for k in safe]
        params = list(safe.values()) + [entry_id]
        with self._lock, self._conn:
            self._conn.execute(
                f"UPDATE vocab_entry SET {', '.join(sets)} WHERE id = ?",
                params,
            )

    def rename_entry(
        self, entry_id: int, new_variant: str, new_term: str,
    ) -> dict | None:
        """Rename an entry's variant/term in place, preserving stats and frequency.

        Returns None if the target (variant, term) already exists (UNIQUE conflict).
        """
        try:
            with self._lock, self._conn:
                self._conn.execute(
                    "UPDATE vocab_entry SET variant = ?, term = ?, last_updated = ? "
                    "WHERE id = ?",
                    (new_variant, new_term, _now_iso(), entry_id),
                )
                row = self._conn.execute(
                    "SELECT * FROM vocab_entry WHERE id = ?", (entry_id,),
                ).fetchone()
            return dict(row) if row else None
        except sqlite3.IntegrityError:
            logger.warning(
                "Cannot rename entry %d to (%s, %s): pair already exists",
                entry_id, new_variant, new_term,
            )
            return None

    @property
    def entry_count(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM vocab_entry").fetchone()
        return row[0]

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    def record_stats(self, entries: list[tuple[int, str, str]]) -> None:
        """Batch UPSERT statistics.

        Parameters
        ----------
        entries : list of (entry_id, metric, context_key)
            Each tuple increments the counter for the given bucket by 1.
        """
        now = _now_iso()
        with self._lock, self._conn:
            self._conn.executemany(
                """INSERT INTO vocab_stats (entry_id, metric, context_key, count, last_time)
                   VALUES (?, ?, ?, 1, ?)
                   ON CONFLICT(entry_id, metric, context_key)
                   DO UPDATE SET count = count + 1, last_time = excluded.last_time""",
                [(eid, metric, ctx, now) for eid, metric, ctx in entries],
            )

    def get_stats(self, entry_id: int) -> list[dict]:
        """Return all stats rows for an entry."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT metric, context_key, count, last_time "
                "FROM vocab_stats WHERE entry_id = ? ORDER BY metric, count DESC",
                (entry_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_all_stats(self) -> dict[int, list[dict]]:
        """Return all stats rows grouped by entry_id.

        Returns a dict mapping entry_id to a list of stat dicts,
        each with keys: metric, context_key, count, last_time.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT entry_id, metric, context_key, count, last_time "
                "FROM vocab_stats ORDER BY entry_id, metric, count DESC",
            ).fetchall()
        result: dict[int, list[dict]] = {}
        for r in rows:
            d = dict(r)
            eid = d.pop("entry_id")
            result.setdefault(eid, []).append(d)
        return result

    def import_stats(self, entry_id: int, stats_rows: list[dict]) -> None:
        """Import stats rows for an entry using upsert (keep max count, latest time)."""
        with self._lock, self._conn:
            self._conn.executemany(
                """INSERT INTO vocab_stats (entry_id, metric, context_key, count, last_time)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(entry_id, metric, context_key)
                   DO UPDATE SET
                       count = MAX(count, excluded.count),
                       last_time = CASE
                           WHEN excluded.last_time > last_time THEN excluded.last_time
                           ELSE last_time
                       END""",
                [
                    (entry_id, s["metric"], s["context_key"], s["count"], s["last_time"])
                    for s in stats_rows
                    if s.get("metric") and s.get("context_key")
                ],
            )

    def get_stats_summary(self, entry_id: int, metric: str) -> int:
        """Sum all buckets for a given entry and metric."""
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(SUM(count), 0) FROM vocab_stats "
                "WHERE entry_id = ? AND metric = ?",
                (entry_id, metric),
            ).fetchone()
        return row[0]

    def get_stats_summary_batch(
        self,
        entry_ids: list[int],
        metrics: list[str],
        context_key: str = "",
        *,
        exclude_app: bool = False,
    ) -> dict[tuple[int, str], int]:
        """Batch fetch stats summaries for multiple entries and metrics.

        When *context_key* is given, only that bucket is counted;
        otherwise all buckets are summed.  When *exclude_app* is True,
        ``app:*`` context keys are excluded from aggregation.

        Returns a dict mapping ``(entry_id, metric)`` to the summed count.
        """
        if not entry_ids or not metrics:
            return {}
        id_ph = ",".join("?" * len(entry_ids))
        m_ph = ",".join("?" * len(metrics))
        where = (
            f"entry_id IN ({id_ph}) AND metric IN ({m_ph})"
        )
        params: list = [*entry_ids, *metrics]
        if context_key:
            where += " AND context_key = ?"
            params.append(context_key)
        elif exclude_app:
            where += f" AND context_key NOT LIKE '{CTX_APP}:%'"
        with self._lock:
            rows = self._conn.execute(
                f"SELECT entry_id, metric, COALESCE(SUM(count), 0) AS total "
                f"FROM vocab_stats WHERE {where} "
                f"GROUP BY entry_id, metric",
                params,
            ).fetchall()
        return {(r["entry_id"], r["metric"]): r["total"] for r in rows}

    # ------------------------------------------------------------------
    # Ranked queries for injection selection
    # ------------------------------------------------------------------

    @staticmethod
    def _collect_rows(
        rows, limit: int, exclude_ids: set[int] | None = None,
    ) -> list[dict]:
        """Filter rows by exclude_ids and collect up to limit results."""
        exclude = exclude_ids or set()
        result = []
        for r in rows:
            if r["id"] in exclude:
                continue
            result.append(dict(r))
            if len(result) >= limit:
                break
        return result

    def top_by_metric(
        self,
        metric: str,
        context_key: str,
        limit: int,
        *,
        exclude_ids: set[int] | None = None,
    ) -> list[dict]:
        """Return top-N entries ranked by *metric* in *context_key* bucket.

        Each returned dict has entry fields plus ``stat_count``.
        Entries in *exclude_ids* are skipped.
        """
        fetch_limit = limit + len(exclude_ids or set())
        with self._lock:
            rows = self._conn.execute(
                """SELECT e.*, s.count AS stat_count
                   FROM vocab_stats s
                   JOIN vocab_entry e ON e.id = s.entry_id
                   WHERE s.metric = ? AND s.context_key = ?
                   ORDER BY s.count DESC
                   LIMIT ?""",
                (metric, context_key, fetch_limit),
            ).fetchall()
        return self._collect_rows(rows, limit, exclude_ids)

    def top_by_metric_global(
        self,
        metric: str,
        limit: int,
        *,
        exclude_ids: set[int] | None = None,
        exclude_app: bool = False,
    ) -> list[dict]:
        """Return top-N entries ranked by global sum of *metric* across all buckets.

        Each returned dict has entry fields plus ``stat_count``.
        When *exclude_app* is True, ``app:*`` context keys are excluded.
        """
        fetch_limit = limit + len(exclude_ids or set())
        where = "s.metric = ?"
        params: list = [metric]
        if exclude_app:
            where += f" AND s.context_key NOT LIKE '{CTX_APP}:%'"
        with self._lock:
            rows = self._conn.execute(
                f"""SELECT e.*, COALESCE(SUM(s.count), 0) AS stat_count
                   FROM vocab_stats s
                   JOIN vocab_entry e ON e.id = s.entry_id
                   WHERE {where}
                   GROUP BY s.entry_id
                   ORDER BY stat_count DESC
                   LIMIT ?""",
                (*params, fetch_limit),
            ).fetchall()
        return self._collect_rows(rows, limit, exclude_ids)

    def top_by_recency(
        self,
        limit: int,
        *,
        exclude_ids: set[int] | None = None,
    ) -> list[dict]:
        """Fallback: return entries ordered by last_updated DESC.

        Each returned dict has entry fields plus ``stat_count`` = 0.
        """
        fetch_limit = limit + len(exclude_ids or set())
        with self._lock:
            rows = self._conn.execute(
                "SELECT *, 0 AS stat_count FROM vocab_entry "
                "ORDER BY last_updated DESC LIMIT ?",
                (fetch_limit,),
            ).fetchall()
        return self._collect_rows(rows, limit, exclude_ids)

    def top_with_fallback(
        self,
        metric: str,
        context_key: str,
        limit: int,
        *,
        exclude_app: bool = False,
    ) -> list[dict]:
        """Three-tier ranked query with cold-start fallback.

        1. Specific bucket query
        2. Global aggregation fill
        3. Recency-based fill
        """
        seen: set[int] = set()
        result: list[dict] = []

        # Tier 1: specific bucket
        if context_key:
            for d in self.top_by_metric(metric, context_key, limit):
                seen.add(d["id"])
                result.append(d)

        # Tier 2: global aggregation
        remaining = limit - len(result)
        if remaining > 0:
            for d in self.top_by_metric_global(
                metric, remaining, exclude_ids=seen, exclude_app=exclude_app,
            ):
                seen.add(d["id"])
                result.append(d)

        # Tier 3: recency fallback
        remaining = limit - len(result)
        if remaining > 0:
            result.extend(self.top_by_recency(remaining, exclude_ids=seen))

        return result[:limit]

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        self._conn.close()
