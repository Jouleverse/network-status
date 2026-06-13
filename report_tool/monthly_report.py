#!/usr/bin/env python3
import argparse
import calendar
import sqlite3
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = REPO_ROOT / "report_tool" / "data" / "network_status.sqlite"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "report_tool" / "output"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate monthly markdown reports from SQLite.")
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


def pct(numerator: int | float, denominator: int | float) -> str:
    if not denominator:
        return "0.00%"
    return f"{(numerator / denominator) * 100:.2f}%"


def md_table(headers: list[str], rows: list[list[str]]) -> str:
    header_line = "| " + " | ".join(headers) + " |"
    separator = "| " + " | ".join(["---"] * len(headers)) + " |"
    body = "\n".join("| " + " | ".join(row) + " |" for row in rows)
    return "\n".join([header_line, separator, body]) if body else "\n".join([header_line, separator])


def build_report(conn: sqlite3.Connection, month_text: str) -> str:
    start_date, end_date = month_bounds(month_text)
    title = f"{month_text} 网络状态月报"

    overview = fetch_one(
        conn,
        """
        WITH daily AS (
            SELECT
                report_date,
                COUNT(*) AS node_count,
                SUM(CASE WHEN status_text = 'connected' THEN 1 ELSE 0 END) AS connected_count,
                SUM(CASE WHEN status_text <> 'connected' THEN 1 ELSE 0 END) AS disconnected_count,
                SUM(check_in_flag) AS checked_in_count
            FROM v_node_daily_latest
            WHERE report_date >= ? AND report_date < ?
            GROUP BY report_date
        )
        SELECT
            COUNT(*) AS days,
            SUM(node_count) AS total_node_rows,
            SUM(connected_count) AS total_connected_rows,
            SUM(disconnected_count) AS total_disconnected_rows,
            SUM(checked_in_count) AS total_checked_in_rows,
            ROUND(AVG(node_count), 2) AS avg_nodes_per_day,
            ROUND(AVG(connected_count), 2) AS avg_connected_per_day,
            ROUND(AVG(disconnected_count), 2) AS avg_disconnected_per_day,
            ROUND(AVG(checked_in_count), 2) AS avg_checked_in_per_day
        FROM daily
        """,
        (start_date, end_date),
    )
    snapshots = fetch_one(
        conn,
        """
        SELECT
            COUNT(*) AS snapshots,
            MIN(report_date) AS first_day,
            MAX(report_date) AS last_day,
            MIN(latest_block_height) AS min_block_height,
            MAX(latest_block_height) AS max_block_height
        FROM report_snapshot
        WHERE report_date >= ? AND report_date < ?
        """,
        (start_date, end_date),
    )
    monthly_checkin = fetch_one(
        conn,
        """
        WITH ordered AS (
            SELECT
                node_type,
                COALESCE(core_id, owner) AS node_key,
                report_date,
                check_in_flag,
                LAG(check_in_flag) OVER (
                    PARTITION BY node_type, COALESCE(core_id, owner)
                    ORDER BY report_date
                ) AS prev_check_in_flag
            FROM v_node_daily_latest
        ),
        monthly_node AS (
            SELECT
                node_type,
                node_key,
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
            GROUP BY node_type, node_key
        )
        SELECT
            COUNT(*) AS distinct_nodes,
            SUM(signed_in_this_month) AS completed_nodes
        FROM monthly_node
        WHERE EXISTS (
            SELECT 1
            FROM v_node_daily_latest v
            WHERE v.report_date >= ? AND v.report_date < ?
              AND v.node_type = monthly_node.node_type
              AND COALESCE(v.core_id, v.owner) = monthly_node.node_key
        )
        """,
        (start_date, end_date, end_date, start_date, end_date),
    )
    by_type = fetch_all(
        conn,
        """
        WITH ordered AS (
            SELECT
                node_type,
                COALESCE(core_id, owner) AS node_key,
                report_date,
                status_text,
                check_in_flag,
                LAG(check_in_flag) OVER (
                    PARTITION BY node_type, COALESCE(core_id, owner)
                    ORDER BY report_date
                ) AS prev_check_in_flag
            FROM v_node_daily_latest
        ),
        monthly_node AS (
            SELECT
                node_type,
                node_key,
                SUM(CASE WHEN report_date >= ? AND report_date < ? THEN 1 ELSE 0 END) AS days_seen,
                SUM(
                    CASE
                        WHEN report_date >= ? AND report_date < ? AND status_text = 'connected'
                        THEN 1 ELSE 0
                    END
                ) AS connected_rows,
                SUM(
                    CASE
                        WHEN report_date >= ? AND report_date < ? AND status_text <> 'connected'
                        THEN 1 ELSE 0
                    END
                ) AS disconnected_rows,
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
            GROUP BY node_type, node_key
        )
        SELECT
            node_type,
            SUM(days_seen) AS node_rows,
            COUNT(*) AS distinct_nodes,
            SUM(connected_rows) AS connected_rows,
            SUM(disconnected_rows) AS disconnected_rows,
            SUM(signed_in_this_month) AS completed_nodes
        FROM monthly_node
        WHERE days_seen > 0
        GROUP BY node_type
        ORDER BY node_type
        """,
        (
            start_date,
            end_date,
            start_date,
            end_date,
            start_date,
            end_date,
            start_date,
            end_date,
            end_date,
        ),
    )
    top_disconnected = fetch_all(
        conn,
        """
        WITH ordered AS (
            SELECT
                COALESCE(core_id, '-') AS core_id,
                owner,
                node_type,
                COALESCE(core_id, owner) AS node_key,
                report_date,
                status_text,
                check_in_flag,
                LAG(check_in_flag) OVER (
                    PARTITION BY node_type, COALESCE(core_id, owner)
                    ORDER BY report_date
                ) AS prev_check_in_flag
            FROM v_node_daily_latest
        )
        SELECT
            core_id,
            owner,
            node_type,
            SUM(CASE WHEN report_date >= ? AND report_date < ? THEN 1 ELSE 0 END) AS days_seen,
            SUM(
                CASE
                    WHEN report_date >= ? AND report_date < ? AND status_text <> 'connected'
                    THEN 1 ELSE 0
                END
            ) AS disconnected_days,
            SUM(
                CASE
                    WHEN report_date >= ? AND report_date < ? AND status_text = 'connected'
                    THEN 1 ELSE 0
                END
            ) AS connected_days,
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
        GROUP BY core_id, owner, node_type, node_key
        HAVING disconnected_days > 0
        ORDER BY disconnected_days DESC, signed_in_this_month ASC, node_type, core_id
        LIMIT 15
        """,
        (
            start_date,
            end_date,
            start_date,
            end_date,
            start_date,
            end_date,
            start_date,
            end_date,
            end_date,
        ),
    )
    incomplete_checkin = fetch_all(
        conn,
        """
        WITH ordered AS (
            SELECT
                COALESCE(core_id, '-') AS core_id,
                owner,
                node_type,
                COALESCE(core_id, owner) AS node_key,
                report_date,
                status_text,
                check_in_flag,
                LAG(check_in_flag) OVER (
                    PARTITION BY node_type, COALESCE(core_id, owner)
                    ORDER BY report_date
                ) AS prev_check_in_flag
            FROM v_node_daily_latest
        ),
        monthly_node AS (
            SELECT
                core_id,
                owner,
                node_type,
                node_key,
                SUM(CASE WHEN report_date >= ? AND report_date < ? THEN 1 ELSE 0 END) AS days_seen,
                MAX(
                    CASE
                        WHEN report_date >= ? AND report_date < ?
                         AND check_in_flag = 1
                         AND COALESCE(prev_check_in_flag, 0) = 0
                        THEN 1
                        ELSE 0
                    END
                ) AS signed_in_this_month,
                SUM(
                    CASE
                        WHEN report_date >= ? AND report_date < ? AND status_text <> 'connected'
                        THEN 1 ELSE 0
                    END
                ) AS disconnected_days
            FROM ordered
            WHERE report_date < ?
            GROUP BY core_id, owner, node_type, node_key
        )
        SELECT
            core_id,
            owner,
            node_type,
            days_seen,
            signed_in_this_month,
            disconnected_days
        FROM monthly_node
        WHERE days_seen > 0
          AND signed_in_this_month = 0
        ORDER BY disconnected_days DESC, node_type, core_id
        LIMIT 20
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
    daily = fetch_all(
        conn,
        """
        WITH daily AS (
            SELECT
                report_date,
                COUNT(*) AS node_count,
                SUM(CASE WHEN status_text = 'connected' THEN 1 ELSE 0 END) AS connected_count,
                SUM(CASE WHEN status_text <> 'connected' THEN 1 ELSE 0 END) AS disconnected_count,
                SUM(check_in_flag) AS checked_in_count
            FROM v_node_daily_latest
            WHERE report_date >= ? AND report_date < ?
            GROUP BY report_date
        )
        SELECT * FROM daily ORDER BY report_date
        """,
        (start_date, end_date),
    )

    year, month = map(int, month_text.split("-"))
    month_label = f"{year}年{month}月"
    lines = [
        f"# {title}",
        "",
        f"- 月份: {month_label}",
        f"- 快照数: {snapshots.get('snapshots', 0)}",
        f"- 覆盖区间: {snapshots.get('first_day', '-')} 到 {snapshots.get('last_day', '-')}",
        f"- 区块高度范围: {snapshots.get('min_block_height', '-')} ~ {snapshots.get('max_block_height', '-')}",
        "",
        "## 概览",
        "",
        md_table(
            ["指标", "数值"],
            [
                ["统计天数", str(overview.get("days", 0))],
                ["总节点日记录", str(overview.get("total_node_rows", 0))],
                ["总在线记录", str(overview.get("total_connected_rows", 0))],
                ["总离线记录", str(overview.get("total_disconnected_rows", 0))],
                ["当月有新签到节点数", str(monthly_checkin.get("completed_nodes", 0))],
                ["当月无新签到节点数", str(monthly_checkin.get("distinct_nodes", 0) - monthly_checkin.get("completed_nodes", 0))],
                ["平均每日节点数", str(overview.get("avg_nodes_per_day", 0))],
                ["平均每日在线数", str(overview.get("avg_connected_per_day", 0))],
                ["平均每日离线数", str(overview.get("avg_disconnected_per_day", 0))],
                ["平均每日有效签到节点数", str(overview.get("avg_checked_in_per_day", 0))],
                [
                    "在线率",
                    pct(overview.get("total_connected_rows", 0), overview.get("total_node_rows", 0)),
                ],
                [
                    "当月新签到完成率",
                    pct(monthly_checkin.get("completed_nodes", 0), monthly_checkin.get("distinct_nodes", 0)),
                ],
            ],
        ),
        "",
        "## 按类型汇总",
        "",
        md_table(
            ["类型", "记录数", "节点数", "在线数", "离线数", "在线率", "当月新签到节点数", "当月新签到完成率"],
            [
                [
                    row["node_type"],
                    str(row["node_rows"]),
                    str(row["distinct_nodes"]),
                    str(row["connected_rows"]),
                    str(row["disconnected_rows"]),
                    pct(row["connected_rows"], row["node_rows"]),
                    str(row["completed_nodes"]),
                    pct(row["completed_nodes"], row["distinct_nodes"]),
                ]
                for row in by_type
            ],
        ),
        "",
        "## 离线最多节点",
        "",
        md_table(
            ["CORE-ID", "OWNER", "类型", "出现天数", "离线天数", "在线天数", "当月有新签到"],
            [
                [
                    str(row["core_id"]),
                    row["owner"],
                    row["node_type"],
                    str(row["days_seen"]),
                    str(row["disconnected_days"]),
                    str(row["connected_days"]),
                    "是" if row["signed_in_this_month"] else "否",
                ]
                for row in top_disconnected
            ],
        ),
        "",
        "## 当月无新签到节点",
        "",
        md_table(
            ["CORE-ID", "OWNER", "类型", "出现天数", "当月有新签到", "离线天数"],
            [
                [
                    str(row["core_id"]),
                    row["owner"],
                    row["node_type"],
                    str(row["days_seen"]),
                    "是" if row["signed_in_this_month"] else "否",
                    str(row["disconnected_days"]),
                ]
                for row in incomplete_checkin
            ],
        ),
        "",
        "## 每日汇总",
        "",
        md_table(
            ["日期", "节点数", "在线数", "离线数", "在线率", "当日有效签到节点数", "当日有效签到占比"],
            [
                [
                    row["report_date"],
                    str(row["node_count"]),
                    str(row["connected_count"]),
                    str(row["disconnected_count"]),
                    pct(row["connected_count"], row["node_count"]),
                    str(row["checked_in_count"]),
                    pct(row["checked_in_count"], row["node_count"]),
                ]
                for row in daily
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
            output_path = output_dir / f"{month_text}-report.md"
            output_path.write_text(report, encoding="utf-8")
            print(output_path)


if __name__ == "__main__":
    main()
