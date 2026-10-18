"""SQLite persistence for analyses (enough for thousands of builds on a single server)."""
import sqlite3
import threading
from pathlib import Path

from .models import AnalysisRecord, BuildAnalysis

SCHEMA = """
CREATE TABLE IF NOT EXISTS analyses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job TEXT NOT NULL,
    build_number INTEGER NOT NULL,
    build_url TEXT,
    fingerprint TEXT,
    category TEXT,
    severity TEXT,
    confidence REAL,
    analysis_json TEXT NOT NULL,
    jira_key TEXT,
    gerrit_commented INTEGER DEFAULT 0,
    llm_input_tokens INTEGER DEFAULT 0,
    llm_output_tokens INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(job, build_number)
);
CREATE INDEX IF NOT EXISTS idx_fp ON analyses(fingerprint);
CREATE INDEX IF NOT EXISTS idx_cat ON analyses(category);
"""


class Store:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.conn.executescript(SCHEMA)

    def exists(self, job: str, number: int) -> bool:
        with self.lock:
            return self.conn.execute(
                "SELECT 1 FROM analyses WHERE job=? AND build_number=?", (job, number)
            ).fetchone() is not None

    def save(self, rec: AnalysisRecord) -> int:
        a = rec.analysis
        with self.lock, self.conn:
            cur = self.conn.execute(
                """INSERT INTO analyses (job, build_number, build_url, fingerprint, category, severity,
                       confidence, analysis_json, jira_key, gerrit_commented, llm_input_tokens, llm_output_tokens)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(job, build_number) DO UPDATE SET
                       fingerprint=excluded.fingerprint, category=excluded.category,
                       severity=excluded.severity, confidence=excluded.confidence,
                       analysis_json=excluded.analysis_json, jira_key=excluded.jira_key,
                       gerrit_commented=excluded.gerrit_commented""",
                (rec.job, rec.build_number, rec.build_url, rec.fingerprint, a.category.value,
                 a.severity.value, a.confidence, a.model_dump_json(), rec.jira_key,
                 int(rec.gerrit_commented), rec.llm_input_tokens, rec.llm_output_tokens),
            )
            return cur.lastrowid

    @staticmethod
    def _row(r: sqlite3.Row) -> AnalysisRecord:
        return AnalysisRecord(
            id=r["id"], job=r["job"], build_number=r["build_number"], build_url=r["build_url"],
            fingerprint=r["fingerprint"], analysis=BuildAnalysis.model_validate_json(r["analysis_json"]),
            jira_key=r["jira_key"], gerrit_commented=bool(r["gerrit_commented"]),
            created_at=r["created_at"], llm_input_tokens=r["llm_input_tokens"],
            llm_output_tokens=r["llm_output_tokens"],
        )

    def get(self, job: str, number: int) -> AnalysisRecord | None:
        with self.lock:
            r = self.conn.execute(
                "SELECT * FROM analyses WHERE job=? AND build_number=?", (job, number)
            ).fetchone()
        return self._row(r) if r else None

    def recent(self, limit: int = 50, job: str | None = None, category: str | None = None) -> list[AnalysisRecord]:
        q, args = "SELECT * FROM analyses WHERE 1=1", []
        if job:
            q += " AND job=?"; args.append(job)
        if category:
            q += " AND category=?"; args.append(category)
        q += " ORDER BY id DESC LIMIT ?"; args.append(limit)
        with self.lock:
            return [self._row(r) for r in self.conn.execute(q, args).fetchall()]

    def by_fingerprint(self, fp: str, limit: int = 5) -> list[AnalysisRecord]:
        with self.lock:
            rows = self.conn.execute(
                "SELECT * FROM analyses WHERE fingerprint=? ORDER BY id DESC LIMIT ?", (fp, limit)
            ).fetchall()
        return [self._row(r) for r in rows]

    def stats(self) -> dict:
        with self.lock:
            total = self.conn.execute("SELECT COUNT(*) FROM analyses").fetchone()[0]
            cats = self.conn.execute(
                "SELECT category, COUNT(*) c FROM analyses GROUP BY category ORDER BY c DESC").fetchall()
            top = self.conn.execute(
                """SELECT fingerprint, COUNT(*) c, MAX(jira_key) jira FROM analyses
                   GROUP BY fingerprint HAVING c > 1 ORDER BY c DESC LIMIT 10""").fetchall()
            toks = self.conn.execute(
                "SELECT COALESCE(SUM(llm_input_tokens),0), COALESCE(SUM(llm_output_tokens),0) FROM analyses"
            ).fetchone()
        return {
            "total_analyses": total,
            "by_category": {r["category"]: r["c"] for r in cats},
            "recurring_failures": [dict(r) for r in top],
            "llm_tokens": {"input": toks[0], "output": toks[1]},
        }
