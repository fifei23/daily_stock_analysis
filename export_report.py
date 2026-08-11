#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从 daily_stock_analysis 的分析历史数据库 (data/stock_analysis.db) 读取
当日分析结果，渲染成一份 Markdown 报告，保存到 reports/report_YYYYMMDD.md。

为什么需要这个脚本：
    main.py 在不配置通知渠道时不会把「个股决策仪表盘」落盘为文件，
    只写入 analysis_history 表。本脚本把当日记录重建为可阅读/可分享的
    报告，方便在 WorkBuddy 自动化里 present 给用户。

新增：从 AkShare 拉取实时最新价，在总览与个股详情中展示「当前最新价」，
      让买点/卖点与现价直接对比（网络不可用时自动跳过价格列，不报错）。

用法：
    python export_report.py                 # 导出今日全部分析
    python export_report.py 510300,588000   # 仅导出指定代码
"""
import sqlite3
import os
import sys
import subprocess
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).parent
DB = ROOT / "data" / "stock_analysis.db"
REPORTS = ROOT / "reports"
REPORTS.mkdir(parents=True, exist_ok=True)

PY = (ROOT.parent / ".." / "Users" / "ASUS" / ".workbuddy" / "binaries" / "python" /
      "envs" / "daily_stock_analysis" / "Scripts" / "python.exe")
# 上面那条绝对路径仅为备注；实际用当前 python 解释器运行 akshare


def fetch_live_prices():
    """从 AkShare 拉全市场 ETF 实时价，返回 {code: 最新价}。失败时返回空 dict。"""
    try:
        import akshare as ak
        df = ak.fund_etf_spot_em()
        price = {}
        for _, row in df.iterrows():
            code = str(row.get("代码", "")).strip()
            if not code:
                continue
            # 兼容不同列名
            val = None
            for col in ("最新价", "现价", "价格", "trade", "last"):
                if col in row and row[col] not in (None, ""):
                    val = row[col]
                    break
            if val is None:
                continue
            try:
                price[code] = float(val)
            except (ValueError, TypeError):
                continue
        print(f"[INFO] 实时价获取成功，覆盖 {len(price)} 只 ETF。")
        return price
    except Exception as e:
        print(f"[WARN] 实时价获取失败（将不展示现价）：{e}")
        return {}


def fmt_price(p):
    if p is None:
        return "—"
    return f"{p:.3f}"


def main() -> int:
    codes_filter = None
    if len(sys.argv) > 1:
        codes_filter = set(c.strip() for c in sys.argv[1].split(",") if c.strip())

    if not DB.exists():
        print(f"[ERROR] 数据库不存在: {DB}")
        return 1

    today_local = datetime.now().strftime("%Y-%m-%d")
    today_file = datetime.now().strftime("%Y%m%d")

    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT code, name, sentiment_score, operation_advice, trend_prediction,
                   analysis_summary, ideal_buy, secondary_buy, stop_loss, take_profit, created_at
            FROM analysis_history a
            WHERE created_at LIKE ?
              AND created_at = (
                  SELECT MAX(created_at) FROM analysis_history b
                  WHERE b.code = a.code AND b.created_at LIKE ?
              )
            ORDER BY sentiment_score DESC
            """,
            (today_local + "%", today_local + "%"),
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    if codes_filter:
        rows = [r for r in rows if str(r["code"]).strip() in codes_filter]
    if not rows:
        print(f"[INFO] 今日 ({today_local}) 无匹配分析记录，未生成报告。")
        return 0

    prices = fetch_live_prices()

    def price_of(code):
        return prices.get(str(code).strip())

    lines = [
        f"# 📊 ETF 每日智能分析报告（{today_local}）",
        "",
        f"> 数据来源：daily_stock_analysis × 智谱 GLM；共分析 **{len(rows)}** 只标的，按综合评分降序。",
        "> 现价取自 AkShare 实时行情，与买点/卖点并列供参考。",
        "",
        "## 一、决策总览",
        "",
        "| 代码 | 名称 | 操作建议 | 评分 | 趋势 | 现价 |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['code']} | {r['name']} | {r['operation_advice'] or '-'} "
            f"| {r['sentiment_score']} | {r['trend_prediction'] or '-'} "
            f"| {fmt_price(price_of(r['code']))} |"
        )
    lines.append("")
    lines.append("## 二、个股详情")
    lines.append("")

    for r in rows:
        code = str(r["code"]).strip()
        lines.append(f"### {r['name']}（{r['code']}）")
        lines.append(f"- **当前最新价**：{fmt_price(price_of(code))}（AkShare 实时）")
        lines.append(f"- **操作建议**：{r['operation_advice'] or '-'}")
        lines.append(f"- **综合评分**：{r['sentiment_score']}")
        lines.append(f"- **趋势判断**：{r['trend_prediction'] or '-'}")
        for label, key in [
            ("理想买点", "ideal_buy"),
            ("次优买点", "secondary_buy"),
            ("止损位", "stop_loss"),
            ("止盈位", "take_profit"),
        ]:
            v = r[key]
            if v:
                lines.append(f"- **{label}**：{v}")
        if r["analysis_summary"]:
            summary = str(r["analysis_summary"]).replace("\n", " ").strip()
            lines.append(f"- **分析摘要**：{summary}")
        lines.append("")

    out = REPORTS / f"report_{today_file}.md"
    out.write_text("\n".join(lines), encoding="utf-8")

    print(f"[OK] 报告已生成：{out}")
    print(f"[OK] 覆盖标的：{len(rows)} 只")
    for r in rows:
        print(f"    - {r['name']}({r['code']}): {r['operation_advice']} 评分{r['sentiment_score']} 现价{fmt_price(price_of(str(r['code']).strip()))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
