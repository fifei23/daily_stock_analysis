#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从 daily_stock_analysis 的分析历史数据库 (data/stock_analysis.db) 读取当日分析结果，
渲染成一份自包含的 HTML「决策仪表盘」(看板)，保存到 reports/dashboard_YYYYMMDD.html。

特性：
  - 卡片墙：每只 ETF 一张卡，含 操作建议(红涨绿跌) / 评分 / 趋势 / 现价 / 买点 / 止损 / 止盈 / 摘要
  - 顶部统计：买入/持有/卖出 计数 + 平均评分
  - 交互：按 操作建议 筛选；按 评分 / 现价距买点% 排序（纯前端 JS，无外部依赖）
  - 现价取自 AkShare 实时行情，与买点并列；网络不可用时自动显示「—」
  - 单文件、无 CDN，浏览器直接打开即可

用法：
    python dashboard.py                 # 渲染今日全部分析
    python dashboard.py 510300,588000   # 仅指定代码
"""
import sqlite3
import os
import sys
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).parent
DB = ROOT / "data" / "stock_analysis.db"
REPORTS = ROOT / "reports"
REPORTS.mkdir(parents=True, exist_ok=True)


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


def to_float(v):
    if v in (None, "", "None"):
        return None
    try:
        return float(str(v).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def classify_advice(advice):
    """返回 (类别, 显示文案)。类别: buy / sell / hold"""
    a = (advice or "").strip()
    if any(k in a for k in ("买入", "做多", "加仓", "增持", "低吸", "建仓")):
        return "buy", a or "买入"
    if any(k in a for k in ("卖出", "做空", "减仓", "减持", "止盈出", "离场")):
        return "sell", a or "卖出"
    return "hold", a or "持有观望"


def score_class(s):
    try:
        s = float(s)
    except (TypeError, ValueError):
        return "s-mid"
    if s >= 70:
        return "s-high"
    if s >= 55:
        return "s-mid"
    return "s-low"


TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ETF 每日决策仪表盘 __DATE__</title>
<style>
  :root{
    --bg:#f5f7fa; --card:#ffffff; --ink:#1f2329; --sub:#6b7280; --line:#e5e7eb;
    --buy:#e23c3c; --sell:#1aa260; --hold:#e8920c;
    --hi:#d4380d; --mid:#d46b08; --lo:#389e0d;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;}
  .wrap{max-width:1280px;margin:0 auto;padding:20px 18px 60px;}
  header h1{font-size:22px;margin:0 0 4px;}
  header .meta{color:var(--sub);font-size:13px;margin-bottom:16px;}
  .stats{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:18px;}
  .stat{flex:1;min-width:150px;background:var(--card);border:1px solid var(--line);
    border-radius:12px;padding:12px 14px;}
  .stat .n{font-size:26px;font-weight:700;}
  .stat .l{font-size:12px;color:var(--sub);margin-top:2px;}
  .stat.buy .n{color:var(--buy)} .stat.sell .n{color:var(--sell)} .stat.hold .n{color:var(--hold)}
  .toolbar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:16px;}
  .btn{border:1px solid var(--line);background:#fff;border-radius:999px;padding:6px 14px;
    font-size:13px;cursor:pointer;color:var(--ink);}
  .btn.active{background:#1f2329;color:#fff;border-color:#1f2329;}
  .toolbar select{border:1px solid var(--line);border-radius:8px;padding:6px 10px;font-size:13px;background:#fff;}
  .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(290px,1fr));gap:14px;}
  .card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px;
    border-left:5px solid var(--line);}
  .card.buy{border-left-color:var(--buy)} .card.sell{border-left-color:var(--sell)}
  .card.hold{border-left-color:var(--hold)}
  .card .top{display:flex;justify-content:space-between;align-items:flex-start;gap:8px;}
  .card .name{font-size:15px;font-weight:700;line-height:1.2;}
  .card .code{font-size:12px;color:var(--sub);margin-top:2px;}
  .badge{font-size:12px;font-weight:700;padding:3px 10px;border-radius:999px;white-space:nowrap;}
  .badge.buy{background:rgba(226,60,60,.12);color:var(--buy);}
  .badge.sell{background:rgba(26,162,96,.12);color:var(--sell);}
  .badge.hold{background:rgba(232,146,12,.14);color:var(--hold);}
  .score{display:flex;align-items:baseline;gap:6px;margin:10px 0 6px;}
  .score .v{font-size:30px;font-weight:800;}
  .score .t{font-size:12px;color:var(--sub);}
  .s-high{color:var(--hi)} .s-mid{color:var(--mid)} .s-low{color:var(--lo)}
  .price{display:flex;align-items:baseline;gap:8px;margin:6px 0 10px;}
  .price .now{font-size:22px;font-weight:700;}
  .price .gap{font-size:12px;padding:2px 8px;border-radius:6px;}
  .gap.pos{color:var(--buy);background:rgba(226,60,60,.1);}
  .gap.neg{color:var(--sell);background:rgba(26,162,96,.1);}
  .gap.flat{color:var(--sub);background:#eef0f3;}
  .pts{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:8px 0;border-top:1px dashed var(--line);padding-top:10px;}
  .pt{text-align:center;}
  .pt .k{font-size:11px;color:var(--sub);}
  .pt .v{font-size:14px;font-weight:600;margin-top:2px;}
  .pt.buy .v{color:var(--buy)} .pt.sl .v{color:var(--sell)} .pt.tp .v{color:var(--mid)}
  .trend{font-size:12px;color:var(--sub);margin-bottom:6px;}
  .summary{font-size:12px;color:#374151;line-height:1.5;margin-top:8px;
    display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden;}
  .empty{color:var(--sub);padding:40px;text-align:center;}
  footer{margin-top:30px;color:var(--sub);font-size:12px;text-align:center;}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>📊 ETF 每日决策仪表盘</h1>
    <div class="meta">数据日期 __DATE__ · 覆盖 __COUNT__ 只标的 · 来源 daily_stock_analysis × 智谱 GLM · 现价 AkShare 实时</div>
  </header>
  <div class="stats">
    <div class="stat"><div class="n">__COUNT__</div><div class="l">分析标的总数</div></div>
    <div class="stat buy"><div class="n">__N_BUY__</div><div class="l">买入信号</div></div>
    <div class="stat hold"><div class="n">__N_HOLD__</div><div class="l">持有观望</div></div>
    <div class="stat sell"><div class="n">__N_SELL__</div><div class="l">卖出信号</div></div>
    <div class="stat"><div class="n">__AVG__</div><div class="l">平均评分</div></div>
  </div>
  <div class="toolbar">
    <span style="font-size:13px;color:var(--sub)">筛选：</span>
    <button class="btn active" data-filter="all">全部</button>
    <button class="btn" data-filter="buy">买入</button>
    <button class="btn" data-filter="hold">持有</button>
    <button class="btn" data-filter="sell">卖出</button>
    <span style="font-size:13px;color:var(--sub);margin-left:8px">排序：</span>
    <select id="sort">
      <option value="score">评分（高→低）</option>
      <option value="gap">现价距买点%（小→大）</option>
      <option value="code">代码（升序）</option>
    </select>
  </div>
  <div class="grid" id="grid">__CARDS__</div>
  <footer>本看板由本地 daily_stock_analysis 自动生成，仅供研究参考，不构成投资建议。</footer>
</div>
<script>
  const grid = document.getElementById('grid');
  const cards = Array.from(grid.children);
  document.querySelectorAll('.btn[data-filter]').forEach(b=>{
    b.onclick=()=>{
      document.querySelectorAll('.btn[data-filter]').forEach(x=>x.classList.remove('active'));
      b.classList.add('active');
      const f=b.dataset.filter;
      cards.forEach(c=>{ c.style.display = (f==='all'||c.dataset.advice===f)?'':'none'; });
    };
  });
  document.getElementById('sort').onchange=e=>{
    const v=e.target.value;
    const arr=cards.slice();
    arr.sort((a,b)=>{
      if(v==='score') return (+b.dataset.score)-(+a.dataset.score);
      if(v==='gap')   return (+a.dataset.gap)-(+b.dataset.gap);
      return a.dataset.code.localeCompare(b.dataset.code);
    });
    arr.forEach(c=>grid.appendChild(c));
  };
</script>
</body>
</html>
"""


def card_html(r, price):
    code = str(r["code"]).strip()
    name = r["name"] or code
    advice = r["operation_advice"] or ""
    cat, advice_label = classify_advice(advice)
    score = r["sentiment_score"]
    try:
        score_f = float(score)
        score_s = f"{score_f:.0f}"
    except (TypeError, ValueError):
        score_s = str(score)
        score_f = 0
    trend = r["trend_prediction"] or "—"
    now = price.get(code)
    ib = to_float(r["ideal_buy"])
    sl = to_float(r["stop_loss"])
    tp = to_float(r["take_profit"])
    # 距买点%：现价相对理想买点的偏离
    if now is not None and ib:
        gap = (now - ib) / ib * 100.0
        if gap <= 0:
            gap_cls, gap_txt = "neg", f"低于买点 {abs(gap):.1f}%"
        elif gap <= 3:
            gap_cls, gap_txt = "pos", f"贴近买点 +{gap:.1f}%"
        else:
            gap_cls, gap_txt = "flat", f"高于买点 +{gap:.1f}%"
        gap_data = f"{gap:.1f}"
    else:
        gap_cls, gap_txt, gap_data = "flat", "—", "999"
    now_s = f"{now:.3f}" if now is not None else "—"
    ib_s = f"{ib:.3f}" if ib else "—"
    sl_s = f"{sl:.3f}" if sl else "—"
    tp_s = f"{tp:.3f}" if tp else "—"
    summary = (str(r["analysis_summary"]).replace("\n", " ").strip()) if r["analysis_summary"] else ""
    return f"""
    <div class="card {cat}" data-advice="{cat}" data-score="{score_f}" data-gap="{gap_data}" data-code="{code}">
      <div class="top">
        <div>
          <div class="name">{name}</div>
          <div class="code">{code}</div>
        </div>
        <span class="badge {cat}">{advice_label}</span>
      </div>
      <div class="score"><span class="v {score_class(score_f)}">{score_s}</span><span class="t">综合评分</span></div>
      <div class="trend">趋势：{trend}</div>
      <div class="price">
        <span class="now">{now_s}</span>
        <span class="gap {gap_cls}">{gap_txt}</span>
      </div>
      <div class="pts">
        <div class="pt buy"><div class="k">理想买点</div><div class="v">{ib_s}</div></div>
        <div class="pt sl"><div class="k">止损</div><div class="v">{sl_s}</div></div>
        <div class="pt tp"><div class="k">止盈</div><div class="v">{tp_s}</div></div>
      </div>
      {f'<div class="summary">{summary}</div>' if summary else ''}
    </div>"""


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
        print(f"[INFO] 今日 ({today_local}) 无匹配分析记录，未生成看板。")
        return 0

    prices = fetch_live_prices()

    cards = [card_html(r, prices) for r in rows]
    n_buy = sum(1 for r in rows if classify_advice(r["operation_advice"])[0] == "buy")
    n_sell = sum(1 for r in rows if classify_advice(r["operation_advice"])[0] == "sell")
    n_hold = len(rows) - n_buy - n_sell
    scores = [to_float(r["sentiment_score"]) or 0 for r in rows]
    avg = sum(scores) / len(scores) if scores else 0

    html = (TEMPLATE
            .replace("__DATE__", today_local)
            .replace("__COUNT__", str(len(rows)))
            .replace("__N_BUY__", str(n_buy))
            .replace("__N_HOLD__", str(n_hold))
            .replace("__N_SELL__", str(n_sell))
            .replace("__AVG__", f"{avg:.0f}")
            .replace("__CARDS__", "\n".join(cards)))

    out = REPORTS / f"dashboard_{today_file}.html"
    out.write_text(html, encoding="utf-8")
    print(f"[OK] 看板已生成：{out}")
    print(f"[OK] 覆盖标的：{len(rows)} 只（买入 {n_buy} / 持有 {n_hold} / 卖出 {n_sell}），平均评分 {avg:.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
