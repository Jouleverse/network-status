#!/usr/bin/env python3
import argparse
import calendar
import sqlite3
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = REPO_ROOT / "report_tool" / "data" / "network_status.sqlite"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "report_tool" / "output"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate abnormal detail markdown reports from SQLite.")
    parser.add_argument("months", nargs="+", help="Months in YYYY-MM format.")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="SQLite database path.")
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory for generated markdown reports.",
    )
    return parser.parse_args()


def month_bounds(month_text: str) -> tuple[str, str]:
    year, month = map(int, month_text.split("-"))
    start = f"{year:04d}-{month:02d}-01"
    if month == 12:
        end = f"{year + 1:04d}-01-01"
    else:
        end = f"{year:04d}-{month + 1:02d}-01"
    return start, end


def fetch_one(conn: sqlite3.Connection, sql: str, params: tuple) -> dict:
    row = conn.execute(sql, params).fetchone()
    if row is None:
        return {}
    return {key: row[key] for key in row.keys()}


def fetch_all(conn: sqlite3.Connection, sql: str, params: tuple) -> list[dict]:
    rows = conn.execute(sql, params).fetchall()
    return [{key: row[key] for key in row.keys()} for row in rows]


def md_table(headers: list[str], rows: list[list[str]]) -> str:
    header_line = "| " + " | ".join(headers) + " |"
    separator = "| " + " | ".join(["---"] * len(headers)) + " |"
    body = "\n".join("| " + " | ".join(row) + " |" for row in rows)
    return "\n".join([header_line, separator, body]) if body else "\n".join([header_line, separator])


def build_report(conn: sqlite3.Connection, month_text: str) -> str:
    start_date, end_date = month_bounds(month_text)
    year, month = map(int, month_text.split("-"))
    month_label = f"{year}年{month}月"

    disconnected_summary = fetch_one(
        conn,
        """
        SELECT
            COUNT(*) AS abnormal_rows,
            COUNT(DISTINCT report_date) AS abnormal_days,
            COUNT(DISTINCT node_type || '|' || COALESCE(core_id, owner)) AS abnormal_nodes
        FROM v_node_daily_latest
        WHERE report_date >= ? AND report_date < ?
          AND status_text <> 'connected'
        """,
        (start_date, end_date),
    )
    disconnected_by_day = fetch_all(
        conn,
        """
        SELECT
            report_date,
            COUNT(*) AS abnormal_count
        FROM v_node_daily_latest
        WHERE report_date >= ? AND report_date < ?
          AND status_text <> 'connected'
        GROUP BY report_date
        ORDER BY report_date
        """,
        (start_date, end_date),
    )
    abnormal_nodes = fetch_all(
        conn,
        """
        SELECT
            node_type,
            COALESCE(core_id, '-') AS core_id,
            owner,
            COUNT(*) AS abnormal_days,
            MIN(report_date) AS first_abnormal_date,
            MAX(report_date) AS last_abnormal_date,
            GROUP_CONCAT(report_date, ', ') AS abnormal_date_list
        FROM v_node_daily_latest
        WHERE report_date >= ? AND report_date < ?
          AND status_text <> 'connected'
        GROUP BY node_type, COALESCE(core_id, owner), owner
        ORDER BY abnormal_days DESC, node_type, core_id
        """,
        (start_date, end_date),
    )
    abnormal_rows = fetch_all(
        conn,
        """
        SELECT
            report_date,
            node_type,
            COALESCE(core_id, '-') AS core_id,
            owner,
            ip_masked,
            status_text,
            activity_text,
            check_in_flag
        FROM v_node_daily_latest
        WHERE report_date >= ? AND report_date < ?
          AND status_text <> 'connected'
        ORDER BY report_date, node_type, core_id, owner
        """,
        (start_date, end_date),
    )
    no_new_checkin = fetch_all(
        conn,
        """
        WITH ordered AS (
            SELECT
                node_type,
                COALESCE(core_id, '-') AS core_id,
                owner,
                COALESCE(core_id, owner) AS node_key,
                report_date,
                check_in_flag,
                status_text,
                LAG(check_in_flag) OVER (
                    PARTITION BY node_type, COALESCE(core_id, owner)
                    ORDER BY report_date
                ) AS prev_check_in_flag
            FROM v_node_daily_latest
        ),
        monthly_node AS (
            SELECT
                node_type,
                core_id,
                owner,
                node_key,
                SUM(CASE WHEN report_date >= ? AND report_date < ? THEN 1 ELSE 0 END) AS days_seen,
                SUM(
                    CASE
                        WHEN report_date >= ? AND report_date < ? AND status_text <> 'connected'
                        THEN 1 ELSE 0
                    END
                ) AS abnormal_days,
                MAX(
                    CASE
                        WHEN report_date >= ? AND report_date < ?
                         AND check_in_flag = 1
                         AND COALESCE(prev_check_in_flag, 0) = 0
                        THEN 1
                        ELSE 0
                    END
                ) AS signed_in_this_month
            FROM ordered
            WHERE report_date < ?
            GROUP BY node_type, core_id, owner, node_key
        )
        SELECT
            node_type,
            core_id,
            owner,
            days_seen,
            abnormal_days
        FROM monthly_node
        WHERE days_seen > 0
          AND signed_in_this_month = 0
        ORDER BY abnormal_days DESC, node_type, core_id, owner
        """,
        (
            start_date,
            end_date,
            start_date,
            end_date,
            start_date,
            end_date,
            end_date,
        ),
    )

    lines = [
        f"# {month_text} 异常明细报表",
        "",
        f"- 月份: {month_label}",
        "- 异常定义 1: 当日节点状态不是 connected",
        "- 异常定义 2: 当月没有发生新的签到事件（不是沿用上月有效期）",
        "",
        "## 异常概览",
        "",
        md_table(
            ["指标", "数值"],
            [
                ["异常记录数", str(disconnected_summary.get("abnormal_rows", 0))],
                ["发生异常的日期数", str(disconnected_summary.get("abnormal_days", 0))],
                ["发生异常的节点数", str(disconnected_summary.get("abnormal_nodes", 0))],
                ["当月无新签到节点数", str(len(no_new_checkin))],
            ],
        ),
        "",
        "## 按日期统计异常数",
        "",
        md_table(
            ["日期", "异常节点数"],
            [[row["report_date"], str(row["abnormal_count"])] for row in disconnected_by_day],
        ),
        "",
        "## 按节点汇总异常日期",
        "",
        md_table(
            ["类型", "CORE-ID", "OWNER", "异常天数", "首次异常日期", "最后异常日期", "异常日期列表"],
            [
                [
                    row["node_type"],
                    row["core_id"],
                    row["owner"],
                    str(row["abnormal_days"]),
                    row["first_abnormal_date"],
                    row["last_abnormal_date"],
                    row["abnormal_date_list"],
                ]
                for row in abnormal_nodes
            ],
        ),
        "",
        "## 每条异常明细",
        "",
        md_table(
            ["日期", "类型", "CORE-ID", "OWNER", "IP", "状态", "ACTIVITY", "当日有效签到"],
            [
                [
                    row["report_date"],
                    row["node_type"],
                    row["core_id"],
                    row["owner"],
                    row["ip_masked"],
                    row["status_text"],
                    row["activity_text"],
                    "是" if row["check_in_flag"] else "否",
                ]
                for row in abnormal_rows
            ],
        ),
        "",
        "## 当月无新签到节点",
        "",
        md_table(
            ["类型", "CORE-ID", "OWNER", "出现天数", "异常天数"],
            [
                [
                    row["node_type"],
                    row["core_id"],
                    row["owner"],
                    str(row["days_seen"]),
                    str(row["abnormal_days"]),
                ]
                for row in no_new_checkin
            ],
        ),
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(args.db) as conn:
        conn.row_factory = sqlite3.Row
        for month_text in args.months:
            year, month = map(int, month_text.split("-"))
            calendar.monthrange(year, month)
            report = build_report(conn, month_text)
            output_path = output_dir / f"{month_text}-abnormal-details.md"
            output_path.write_text(report, encoding="utf-8")
            print(output_path)


if __name__ == "__main__":
    main()
