"""SQLite persistence for analyses and the review audit trail."""

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime

from ai.schemas import AnalysisResult, AuditEvent, EvidencePackage

_SCHEMA = """
CREATE TABLE IF NOT EXISTS analyses (
    build_id   TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    evidence   TEXT NOT NULL,
    analysis   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_events (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at        TEXT NOT NULL,
    event_type        TEXT NOT NULL,
    build_id          TEXT NOT NULL,
    recommendation_id TEXT,
    decision          TEXT,
    reviewer          TEXT,
    comment           TEXT
);
"""
_AUDIT_BY_ID = (
    "SELECT id, created_at, event_type, build_id, recommendation_id, decision, reviewer, "
    "comment FROM audit_events WHERE id = ?"
)
_AUDIT_BY_BUILD = (
    "SELECT id, created_at, event_type, build_id, recommendation_id, decision, reviewer, "
    "comment FROM audit_events WHERE build_id = ? ORDER BY id"
)
_AUDIT_ALL = (
    "SELECT id, created_at, event_type, build_id, recommendation_id, decision, reviewer, "
    "comment FROM audit_events ORDER BY id"
)


class Store:
    def __init__(self, path: str):
        self.path = path
        with closing(self._connect()) as db, db:
            db.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        return db

    def ping(self) -> bool:
        with closing(self._connect()) as db:
            return db.execute("SELECT 1").fetchone()[0] == 1

    def save_analysis(self, package: EvidencePackage, analysis: AnalysisResult) -> None:
        with closing(self._connect()) as db, db:
            db.execute(
                "INSERT OR REPLACE INTO analyses (build_id, created_at, evidence, analysis) "
                "VALUES (?, ?, ?, ?)",
                (
                    analysis.build_id,
                    analysis.created_at,
                    package.model_dump_json(),
                    analysis.model_dump_json(),
                ),
            )
            self._audit(db, "analysis_recorded", analysis.build_id)

    def get(self, build_id: str) -> dict | None:
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT evidence, analysis FROM analyses WHERE build_id = ?", (build_id,)
            ).fetchone()
        if row is None:
            return None
        return {
            "evidence": json.loads(row["evidence"]),
            "analysis": json.loads(row["analysis"]),
            "reviews": self.latest_reviews(build_id),
        }

    def list_analyses(self) -> list[dict]:
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT evidence, analysis FROM analyses ORDER BY created_at DESC, rowid DESC "
                "LIMIT 200"
            ).fetchall()
        result = []
        for row in rows:
            build = json.loads(row["evidence"])["build"]
            analysis = json.loads(row["analysis"])
            result.append(
                {
                    "build_id": build["id"],
                    "number": build["number"],
                    "scenario": build["scenario"],
                    "jenkins_result": build["result"],
                    "started_at": build["started_at"],
                    "analysis_status": analysis["analysis_status"],
                    "failure_category": analysis["failure_category"],
                    "recommendations": len(analysis["recommended_actions"]),
                    "created_at": analysis["created_at"],
                }
            )
        return result

    def record_review(
        self, build_id: str, recommendation_id: str, decision: str, reviewer: str, comment: str
    ) -> AuditEvent:
        with closing(self._connect()) as db, db:
            event_id = self._audit(
                db, "review_recorded", build_id, recommendation_id, decision, reviewer, comment
            )
            row = db.execute(_AUDIT_BY_ID, (event_id,)).fetchone()
        return AuditEvent(**dict(row))

    def latest_reviews(self, build_id: str) -> dict[str, dict]:
        """Most recent decision per recommendation of the current analysis.

        Decisions made on an analysis that was later replaced do not carry over: the
        recommendation ids may now mean something else. They stay in the audit trail.
        """
        latest = {}
        for event in self.audit(build_id):
            if event.event_type == "analysis_recorded":
                latest = {}
            elif event.event_type == "review_recorded":
                latest[event.recommendation_id] = event.model_dump()
        return latest

    def audit(self, build_id: str | None = None) -> list[AuditEvent]:
        with closing(self._connect()) as db:
            if build_id is None:
                rows = db.execute(_AUDIT_ALL).fetchall()
            else:
                rows = db.execute(_AUDIT_BY_BUILD, (build_id,)).fetchall()
        return [AuditEvent(**dict(row)) for row in rows]

    def stats(self) -> dict:
        analyses = self.list_analyses()
        reviews = [e for e in self.audit() if e.event_type == "review_recorded"]
        return {
            "analyses": len(analyses),
            "by_status": _count(a["analysis_status"] for a in analyses),
            "by_category": _count(a["failure_category"] for a in analyses),
            "review_events": len(reviews),
            "by_decision": _count(e.decision for e in reviews),
        }

    @staticmethod
    def _audit(db, event_type, build_id, recommendation_id=None, decision=None, reviewer=None,
               comment=None) -> int:
        cursor = db.execute(
            "INSERT INTO audit_events (created_at, event_type, build_id, recommendation_id, "
            "decision, reviewer, comment) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                datetime.now(UTC).isoformat(timespec="seconds"),
                event_type,
                build_id,
                recommendation_id,
                decision,
                reviewer,
                comment,
            ),
        )
        return cursor.lastrowid


def _count(values) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return counts
