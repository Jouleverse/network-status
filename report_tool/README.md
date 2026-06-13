# report_tool

独立的网络状态报表小工具目录。

## 目录内容

- `import_daily_report_history.py`: 导入 `daily-report.txt` 的 git 历史到 SQLite
- `monthly_report.py`: 生成月度汇总报表
- `abnormal_detail_report.py`: 生成异常明细报表
- `data/network_status.sqlite`: SQLite 数据库
- `sql/current_month_queries.sql`: 常用 SQL 查询
- `output/`: 动态生成的报表输出目录
- `__init__.py`: 包入口标记

## 用法

在仓库根目录执行。

导入日报历史:

```bash
python3 report_tool/import_daily_report_history.py
```

只导入某个月:

```bash
python3 report_tool/import_daily_report_history.py --month 2026-06
```

生成月报:

```bash
python3 report_tool/monthly_report.py 2026-04 2026-05
```

生成异常明细:

```bash
python3 report_tool/abnormal_detail_report.py 2026-04 2026-05
```

自定义数据库路径:

```bash
python3 report_tool/monthly_report.py 2026-05 --db report_tool/data/network_status.sqlite
```

自定义输出目录:

```bash
python3 report_tool/abnormal_detail_report.py 2026-05 --output-dir reports
```

## 输出

默认写入 `report_tool/output/`:

- `YYYY-MM-report.md`
- `YYYY-MM-abnormal-details.md`

默认数据库路径:

- `report_tool/data/network_status.sqlite`

常用 SQL:

- `report_tool/sql/current_month_queries.sql`
