-- 当月日报快照
SELECT
    report_date,
    report_time,
    commit_hash,
    latest_block_height,
    network_size,
    miner_count,
    witness_count,
    node_row_count
FROM report_snapshot
WHERE report_date >= date('now', 'start of month')
  AND report_date < date('now', 'start of month', '+1 month')
ORDER BY report_time DESC;

-- 当月按节点的最新日状态（每个 core_id + type 每天只保留最后一条）
SELECT
    report_date,
    node_type,
    core_id,
    owner,
    ip_masked,
    status_text,
    activity_text,
    check_in_flag
FROM v_node_daily_latest
WHERE report_date >= date('now', 'start of month')
  AND report_date < date('now', 'start of month', '+1 month')
ORDER BY report_date DESC, node_type, core_id;

-- 当月按 owner / core_id 的在线统计
SELECT
    core_id,
    owner,
    node_type,
    COUNT(*) AS days_seen,
    SUM(CASE WHEN status_text = 'connected' THEN 1 ELSE 0 END) AS connected_days,
    SUM(CASE WHEN status_text <> 'connected' THEN 1 ELSE 0 END) AS disconnected_days,
    ROUND(AVG(check_in_flag), 4) AS check_in_rate
FROM v_node_daily_latest
WHERE report_date >= date('now', 'start of month')
  AND report_date < date('now', 'start of month', '+1 month')
GROUP BY core_id, owner, node_type
ORDER BY node_type, core_id;

-- 当月按天汇总
SELECT
    report_date,
    COUNT(*) AS node_count,
    SUM(CASE WHEN status_text = 'connected' THEN 1 ELSE 0 END) AS connected_count,
    SUM(CASE WHEN status_text <> 'connected' THEN 1 ELSE 0 END) AS disconnected_count,
    SUM(check_in_flag) AS checked_in_count
FROM v_node_daily_latest
WHERE report_date >= date('now', 'start of month')
  AND report_date < date('now', 'start of month', '+1 month')
GROUP BY report_date
ORDER BY report_date DESC;
