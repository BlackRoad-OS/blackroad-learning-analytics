#!/usr/bin/env python3
"""BlackRoad Learning Analytics - student progress, engagement scoring."""

from __future__ import annotations
import argparse
import json
import sqlite3
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path

GREEN = "\033[0;32m"
RED = "\033[0;31m"
CYAN = "\033[0;36m"
YELLOW = "\033[1;33m"
BLUE = "\033[0;34m"
BOLD = "\033[1m"
NC = "\033[0m"

DB_PATH = Path.home() / ".blackroad" / "learning-analytics.db"


@dataclass
class Student:
    id: int
    name: str
    email: str
    cohort: str
    enrolled_at: str
    active: int


@dataclass
class LearningEvent:
    id: int
    student_id: int
    event_type: str
    module: str
    score: float
    duration_minutes: int
    recorded_at: str
    notes: str


@dataclass
class EngagementReport:
    student_id: int
    student_name: str
    total_events: int
    avg_score: float
    total_minutes: int
    engagement_score: float
    grade: str


class LearningAnalytics:
    """Analytics engine for student learning data."""

    GRADE_THRESHOLDS = {"A": 90, "B": 80, "C": 70, "D": 60}

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS students (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    email TEXT UNIQUE NOT NULL,
                    cohort TEXT DEFAULT 'default',
                    enrolled_at TEXT NOT NULL,
                    active INTEGER DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS learning_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    student_id INTEGER NOT NULL REFERENCES students(id),
                    event_type TEXT NOT NULL,
                    module TEXT NOT NULL,
                    score REAL DEFAULT 0,
                    duration_minutes INTEGER DEFAULT 0,
                    recorded_at TEXT NOT NULL,
                    notes TEXT DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_events_student
                    ON learning_events(student_id);
            """)

    def add_student(self, name: str, email: str, cohort: str = "default") -> Student:
        """Enroll a new student."""
        with sqlite3.connect(self.db_path) as conn:
            now = datetime.now().isoformat()
            cur = conn.execute(
                "INSERT INTO students (name,email,cohort,enrolled_at) VALUES (?,?,?,?)",
                (name, email, cohort, now),
            )
            return Student(cur.lastrowid, name, email, cohort, now, 1)

    def record_event(self, student_email: str, event_type: str, module: str,
                     score: float = 0.0, duration: int = 0, notes: str = "") -> LearningEvent:
        """Record a learning event for a student."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT id FROM students WHERE email=?", (student_email,)).fetchone()
            if not row:
                raise ValueError(f"Student '{student_email}' not found")
            student_id = row[0]
            now = datetime.now().isoformat()
            cur = conn.execute(
                "INSERT INTO learning_events"
                " (student_id,event_type,module,score,duration_minutes,recorded_at,notes)"
                " VALUES (?,?,?,?,?,?,?)",
                (student_id, event_type, module, score, duration, now, notes),
            )
            return LearningEvent(cur.lastrowid, student_id, event_type, module,
                                 score, duration, now, notes)

    def list_students(self, cohort: str = None) -> list:
        """Return enrolled students, optionally filtered by cohort."""
        with sqlite3.connect(self.db_path) as conn:
            if cohort:
                rows = conn.execute(
                    "SELECT * FROM students WHERE cohort=? AND active=1", (cohort,)
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM students WHERE active=1").fetchall()
            return [Student(*r) for r in rows]

    def get_engagement_report(self, student_email: str) -> EngagementReport:
        """Compute engagement score for a student (0-100)."""
        with sqlite3.connect(self.db_path) as conn:
            s = conn.execute("SELECT * FROM students WHERE email=?", (student_email,)).fetchone()
            if not s:
                raise ValueError(f"Student '{student_email}' not found")
            student = Student(*s)
            rows = conn.execute(
                "SELECT event_type,score,duration_minutes FROM learning_events WHERE student_id=?",
                (student.id,),
            ).fetchall()

        if not rows:
            return EngagementReport(student.id, student.name, 0, 0.0, 0, 0.0, "N/A")

        total = len(rows)
        avg_score = sum(r[1] for r in rows) / total
        total_minutes = sum(r[2] for r in rows)
        # Weighted: 60% avg score + 20% activity volume (capped at 50 events) + 20% time
        activity_pts = min(total / 50, 1.0) * 100
        time_pts = min(total_minutes / 600, 1.0) * 100  # up to 10 hrs
        engagement = round(avg_score * 0.6 + activity_pts * 0.2 + time_pts * 0.2, 1)

        grade = "F"
        for g, thresh in self.GRADE_THRESHOLDS.items():
            if avg_score >= thresh:
                grade = g
                break

        return EngagementReport(student.id, student.name, total,
                                round(avg_score, 1), total_minutes, engagement, grade)

    def cohort_summary(self, cohort: str) -> dict:
        """Aggregate engagement stats for an entire cohort."""
        students = self.list_students(cohort)
        if not students:
            return {"cohort": cohort, "students": 0}
        reports = [self.get_engagement_report(s.email) for s in students]
        return {
            "cohort": cohort,
            "students": len(reports),
            "avg_engagement": round(sum(r.engagement_score for r in reports) / len(reports), 1),
            "avg_score": round(sum(r.avg_score for r in reports) / len(reports), 1),
            "total_learning_minutes": sum(r.total_minutes for r in reports),
        }

    def status(self) -> dict:
        """Return high-level system statistics."""
        with sqlite3.connect(self.db_path) as conn:
            students = conn.execute("SELECT COUNT(*) FROM students WHERE active=1").fetchone()[0]
            events = conn.execute("SELECT COUNT(*) FROM learning_events").fetchone()[0]
            cohorts = conn.execute(
                "SELECT COUNT(DISTINCT cohort) FROM students WHERE active=1"
            ).fetchone()[0]
        return {"active_students": students, "learning_events": events,
                "cohorts": cohorts, "db_path": str(self.db_path)}

    def export_data(self) -> dict:
        """Export full dataset as JSON."""
        with sqlite3.connect(self.db_path) as conn:
            students = [Student(*r) for r in conn.execute("SELECT * FROM students").fetchall()]
            events = [LearningEvent(*r)
                      for r in conn.execute("SELECT * FROM learning_events").fetchall()]
        return {
            "students": [asdict(s) for s in students],
            "events": [asdict(e) for e in events],
            "exported_at": datetime.now().isoformat(),
        }


def _grade_color(grade: str) -> str:
    return {
        "A": GREEN, "B": CYAN, "C": YELLOW, "D": YELLOW, "F": RED
    }.get(grade, NC)


def _fmt_student(s: Student) -> None:
    print(f"  {CYAN}[{s.id}]{NC} {BOLD}{s.name}{NC}  {s.email}  cohort={YELLOW}{s.cohort}{NC}")


def _fmt_report(r: EngagementReport) -> None:
    gc = _grade_color(r.grade)
    print(f"  {CYAN}[{r.student_id}]{NC} {BOLD}{r.student_name}{NC}"
          f"  events={r.total_events}  avg={r.avg_score:.1f}"
          f"  engagement={GREEN}{r.engagement_score}{NC}"
          f"  grade={gc}{r.grade}{NC}")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="learning_analytics",
        description=f"{BOLD}BlackRoad Learning Analytics{NC}",
    )
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("status", help="System status")
    sub.add_parser("export", help="Export all data as JSON")

    ls = sub.add_parser("list", help="List students")
    ls.add_argument("--cohort", default=None)

    add = sub.add_parser("add", help="Enroll a student")
    add.add_argument("name")
    add.add_argument("email")
    add.add_argument("--cohort", default="default")

    rec = sub.add_parser("record", help="Record a learning event")
    rec.add_argument("email")
    rec.add_argument("event_type", choices=["quiz", "lecture", "assignment", "lab", "exam"])
    rec.add_argument("module")
    rec.add_argument("--score", type=float, default=0.0)
    rec.add_argument("--duration", type=int, default=0)
    rec.add_argument("--notes", default="")

    rep = sub.add_parser("report", help="Engagement report for a student")
    rep.add_argument("email")

    coh = sub.add_parser("cohort", help="Cohort summary")
    coh.add_argument("name")

    args = parser.parse_args()
    la = LearningAnalytics()

    if args.cmd == "list":
        students = la.list_students(args.cohort)
        label = f"cohort={args.cohort}" if args.cohort else "all cohorts"
        print(f"\n{BOLD}{BLUE}Students ({len(students)}) — {label}{NC}")
        [_fmt_student(s) for s in students] or print(f"  {YELLOW}none{NC}")

    elif args.cmd == "add":
        s = la.add_student(args.name, args.email, args.cohort)
        print(f"{GREEN}✓{NC} Enrolled {BOLD}{s.name}{NC} (id={s.id})")

    elif args.cmd == "record":
        ev = la.record_event(args.email, args.event_type, args.module,
                             args.score, args.duration, args.notes)
        print(f"{GREEN}✓{NC} Event recorded (id={ev.id}) score={ev.score}")

    elif args.cmd == "report":
        r = la.get_engagement_report(args.email)
        print(f"\n{BOLD}{BLUE}Engagement Report — {r.student_name}{NC}")
        _fmt_report(r)
        print(f"  Total learning time: {r.total_minutes} min")

    elif args.cmd == "cohort":
        summary = la.cohort_summary(args.name)
        print(f"\n{BOLD}{BLUE}Cohort: {args.name}{NC}")
        for k, v in summary.items():
            print(f"  {CYAN}{k}{NC}: {v}")

    elif args.cmd == "status":
        st = la.status()
        print(f"\n{BOLD}{BLUE}Learning Analytics Status{NC}")
        for k, v in st.items():
            print(f"  {CYAN}{k}{NC}: {GREEN}{v}{NC}")

    elif args.cmd == "export":
        print(json.dumps(la.export_data(), indent=2))

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
