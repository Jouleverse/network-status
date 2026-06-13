#!/usr/bin/env python3
import argparse
import datetime as dt
import json
import re
import sqlite3
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = REPO_ROOT / "report_tool" / "data" / "network_status.sqlite"
FILE_PATH = "daily-report.txt"

REPORT_TIME_RE = re.compile(r"^Report Time:\s*(.+)$")
LATEST_BLOCK_HEIGHT_RE = re.compile(r"^Latest Block Height:\s*(\d+)$")
LATEST_BLOCK_TIME_RE = re.compile(r"^Latest Block Time:\s*(.+)$")
NETWORK_SIZE_RE = re.compile(
    r"^Network Size:\s+(\d+)\s+nodes\s+\(\s*(\d+)\s+miners,\s*(\d+)\s+witnesses,"
)
CURRENT_ROW_RE = re.compile(
    r"^(?P<node_type>\S+)\s+"
    r"(?P<since>\d{8})\s+"
    r"(?P<ip>\S+)\s+"
    r"(?P<connected>\S+)\s+"
    r"(?P<status>\S+)\s+"
    r"(?P<activity>\S+)\s+"
    r"(?P<liveness>\S+)\s+"
    r"(?P<core_id>\S+)\s+"
    r'"(?P<owner>.*?)"\s+'
    r"(?P<check_in>\S+)\s*$"
)
MID_ROW_RE = re.compile(
    r"^(?P<node_type>\S+)\s+"
    r"(?P<since>\d{8})\s+"
    r"(?P<ip>\S+)\s+"
    r"(?P<owner>\S+)\s+"
    r"(?P<check_in>\S+)\s+"
    r"(?P<core_id>\S+)\s+"
    r"(?P<connected>\S+)\s+"
    r"(?P<status>\S+)\s+"
    r"(?P<activity>\S+)\s+"
    r"(?P<liveness>\S+)\s*$"
)
OLD_ROW_RE = re.compile(
    r"^(?P<node_type>\S+)\s+"
    r"(?P<since>\d{8})\s+"
    r"(?P<ip>\S+)\s+"
    r"(?P<owner>\S+)\s+"
    r"(?P<connected>\S+)\s+"
    r"(?P<status>\S+)\s+"
    r"(?P<activity>\S+)\s+"
    r"(?P<liveness>\S+)\s*$"
)

HEADER_CURRENT = "TYPE SINCE IP CONNECTED STATUS ACTIVITY LIVENESS CORE-ID OWNER CHECK-IN"
HEADER_MID = "TYPE SINCE IP OWNER CHECK-IN CORE-ID CONNECTED STATUS ACTIVITY LIVENESS"
HEADER_OLD = "TYPE SINCE IP OWNER CONNECTED STATUS ACTIVITY LIVENESS"


def run_git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import daily-report.txt git history into SQLite.")
    parser.add_argument(
        "--db",
        default=str(DEFAULT_DB),
        help="SQLite database path. Default: %(default)s",
    )
    parser.add_argument(
        "--month",
        default=None,
        help="Optional month filter in YYYY-MM format. Example: 2026-06",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional max commit count to import, in git log order (newest first).",
    )
    return parser.parse_args()


def month_bounds(month_text: str) -> tuple[str, str]:
    month_start = dt.datetime.strptime(month_text, "%Y-%m").date().replace(day=1)
    if month_start.month == 12:
        next_month = month_start.replace(year=month_start.year + 1, month=1)
    else:
        next_month = month_start.replace(month=month_start.month + 1)
    return month_start.isoformat(), next_month.isoformat()


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        PRAGMA journal_mode = WAL;

        CREATE TABLE IF NOT EXISTS report_snapshot (
            snapshot_id INTEGER PRIMARY KEY,
            commit_hash TEXT NOT NULL UNIQUE,
            commit_date TEXT NOT NULL,
            commit_ts TEXT NOT NULL,
            report_time TEXT NOT NULL,
            report_date TEXT NOT NULL,
            latest_block_height INTEGER,
            latest_block_time TEXT,
            network_size INTEGER,
            miner_count INTEGER,
            witness_count INTEGER,
            node_row_count INTEGER NOT NULL,
            raw_header_json TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS node_status (
            node_status_id INTEGER PRIMARY KEY,
            snapshot_id INTEGER NOT NULL,
            commit_hash TEXT NOT NULL,
            report_date TEXT NOT NULL,
            report_time TEXT NOT NULL,
            row_num INTEGER NOT NULL,
            node_type TEXT NOT NULL,
            since_yyyymmdd TEXT NOT NULL,
            since_date TEXT NOT NULL,
            ip_masked TEXT NOT NULL,
            connected_icon TEXT NOT NULL,
            status_text TEXT NOT NULL,
            activity_text TEXT NOT NULL,
            activity_num REAL,
            liveness_icon TEXT NOT NULL,
            core_id TEXT,
            owner TEXT NOT NULL,
            check_in_icon TEXT NOT NULL,
            check_in_flag INTEGER NOT NULL,
            FOREIGN KEY (snapshot_id) REFERENCES report_snapshot(snapshot_id),
            UNIQUE (snapshot_id, row_num)
        );

        CREATE INDEX IF NOT EXISTS idx_report_snapshot_report_date
            ON report_snapshot(report_date);

        CREATE INDEX IF NOT EXISTS idx_node_status_report_date
            ON node_status(report_date);

        CREATE INDEX IF NOT EXISTS idx_node_status_core_id
            ON node_status(core_id);

        CREATE INDEX IF NOT EXISTS idx_node_status_owner
            ON node_status(owner);

        CREATE VIEW IF NOT EXISTS v_node_daily_latest AS
        WITH ranked AS (
            SELECT
                ns.*,
                ROW_NUMBER() OVER (
                    PARTITION BY ns.report_date, COALESCE(ns.core_id, ns.owner), ns.node_type
                    ORDER BY ns.report_time DESC, ns.row_num DESC
                ) AS rn
            FROM node_status ns
        )
        SELECT *
        FROM ranked
        WHERE rn = 1;
        """
    )


def iter_commits(limit: int | None) -> list[tuple[str, str, str]]:
    output = run_git(
        "log",
        "--date=iso-strict",
        "--pretty=format:%H\t%ad\t%cI",
        "--",
        FILE_PATH,
    )
    commits: list[tuple[str, str, str]] = []
    for line in output.splitlines():
        commit_hash, author_date, committer_ts = line.split("\t")
        commit_date = author_date[:10]
        commits.append((commit_hash, commit_date, committer_ts))
        if limit is not None and len(commits) >= limit:
            break
    return commits


def extract_report(commit_hash: str) -> str:
    return run_git("show", f"{commit_hash}:{FILE_PATH}")


def normalize_since(since_text: str) -> str:
    return dt.datetime.strptime(since_text, "%Y%m%d").date().isoformat()


def maybe_number(value: str) -> float | None:
    try:
        return float(value)
    except ValueError:
        return None


def normalize_core_id(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    if value.startswith("J-"):
        return value
    if value.isdigit():
        return f"J-{value}"
    return value


def parse_node_row(line: str, header_type: str) -> dict | None:
    if header_type == HEADER_CURRENT:
        match = CURRENT_ROW_RE.match(line)
        if not match:
            return None
        row = match.groupdict()
        row["core_id"] = normalize_core_id(row["core_id"])
        row["owner"] = row["owner"].strip()
        return row

    if header_type == HEADER_MID:
        match = MID_ROW_RE.match(line)
        if not match:
            return None
        row = match.groupdict()
        row["core_id"] = normalize_core_id(row["core_id"])
        row["owner"] = row["owner"].strip()
        return row

    if header_type == HEADER_OLD:
        match = OLD_ROW_RE.match(line)
        if not match:
            return None
        row = match.groupdict()
        row["core_id"] = None
        row["check_in"] = row["liveness"]
        row["liveness"] = row["connected"]
        row["owner"] = row["owner"].strip()
        return row

    return None


def parse_report_text(text: str) -> tuple[dict, list[dict]]:
    lines = [line.rstrip("\n") for line in text.splitlines()]
    report_time = None
    latest_block_height = None
    latest_block_time = None
    network_size = None
    miner_count = None
    witness_count = None
    node_rows: list[dict] = []
    in_table = False
    header_type = None

    for line in lines:
        if not line:
            continue
        if (match := REPORT_TIME_RE.match(line)):
            report_time = match.group(1)
            continue
        if (match := LATEST_BLOCK_HEIGHT_RE.match(line)):
            latest_block_height = int(match.group(1))
            continue
        if (match := LATEST_BLOCK_TIME_RE.match(line)):
            latest_block_time = match.group(1)
            continue
        if (match := NETWORK_SIZE_RE.match(line)):
            network_size = int(match.group(1))
            miner_count = int(match.group(2))
            witness_count = int(match.group(3))
            continue
        if line in {HEADER_CURRENT, HEADER_MID, HEADER_OLD}:
            in_table = True
            header_type = line
            continue
        if in_table and line.startswith("---------------- notice "):
            break
        if not in_table or line.startswith("---"):
            continue
        if header_type and (row := parse_node_row(line, header_type)):
            node_rows.append(row)

    if report_time is None:
        raise ValueError("Missing Report Time")

    report_dt = dt.datetime.strptime(report_time, "%Y-%m-%d %H:%M:%S %z")
    header = {
        "report_time": report_time,
        "report_date": report_dt.date().isoformat(),
        "latest_block_height": latest_block_height,
        "latest_block_time": latest_block_time,
        "network_size": network_size,
        "miner_count": miner_count,
        "witness_count": witness_count,
    }
    return header, node_rows


def insert_snapshot(
    conn: sqlite3.Connection,
    commit_hash: str,
    commit_date: str,
    commit_ts: str,
    header: dict,
    node_rows: list[dict],
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO report_snapshot (
            commit_hash, commit_date, commit_ts, report_time, report_date,
            latest_block_height, latest_block_time, network_size,
            miner_count, witness_count, node_row_count, raw_header_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            commit_hash,
            commit_date,
            commit_ts,
            header["report_time"],
            header["report_date"],
            header["latest_block_height"],
            header["latest_block_time"],
            header["network_size"],
            header["miner_count"],
            header["witness_count"],
            len(node_rows),
            json.dumps(header, ensure_ascii=False, sort_keys=True),
        ),
    )
    snapshot_id = conn.execute(
        "SELECT snapshot_id FROM report_snapshot WHERE commit_hash = ?",
        (commit_hash,),
    ).fetchone()[0]
    conn.execute("DELETE FROM node_status WHERE snapshot_id = ?", (snapshot_id,))
    conn.executemany(
        """
        INSERT INTO node_status (
            snapshot_id, commit_hash, report_date, report_time, row_num,
            node_type, since_yyyymmdd, since_date, ip_masked, connected_icon,
            status_text, activity_text, activity_num, liveness_icon, core_id,
            owner, check_in_icon, check_in_flag
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                snapshot_id,
                commit_hash,
                header["report_date"],
                header["report_time"],
                idx,
                row["node_type"],
                row["since"],
                normalize_since(row["since"]),
                row["ip"],
                row["connected"],
                row["status"],
                row["activity"],
                maybe_number(row["activity"]),
                row["liveness"],
                row["core_id"],
                row["owner"],
                row["check_in"],
                1 if row["check_in"] == "✅" else 0,
            )
            for idx, row in enumerate(node_rows, start=1)
        ],
    )


def main() -> None:
    args = parse_args()
    db_path = Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    commits = iter_commits(args.limit)
    month_range = month_bounds(args.month) if args.month else None

    with sqlite3.connect(db_path) as conn:
        create_schema(conn)
        imported = 0
        inserted_rows = 0
        skipped_commits: list[dict] = []

        for commit_hash, commit_date, commit_ts in commits:
            if month_range is not None:
                if not (month_range[0] <= commit_date < month_range[1]):
                    continue

            report_text = extract_report(commit_hash)
            try:
                header, node_rows = parse_report_text(report_text)
            except ValueError as exc:
                skipped_commits.append(
                    {
                        "commit_hash": commit_hash,
                        "commit_date": commit_date,
                        "reason": str(exc),
                    }
                )
                continue

            if month_range is not None:
                if not (month_range[0] <= header["report_date"] < month_range[1]):
                    continue

            insert_snapshot(conn, commit_hash, commit_date, commit_ts, header, node_rows)
            imported += 1
            inserted_rows += len(node_rows)

        conn.commit()

        snapshot_total = conn.execute("SELECT COUNT(*) FROM report_snapshot").fetchone()[0]
        node_total = conn.execute("SELECT COUNT(*) FROM node_status").fetchone()[0]
        print(
            json.dumps(
                {
                    "db": str(db_path),
                    "imported_snapshots": imported,
                    "imported_node_rows": inserted_rows,
                    "db_snapshot_total": snapshot_total,
                    "db_node_total": node_total,
                    "month_filter": args.month,
                    "skipped_commit_count": len(skipped_commits),
                    "skipped_commits_preview": skipped_commits[:10],
                },
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
