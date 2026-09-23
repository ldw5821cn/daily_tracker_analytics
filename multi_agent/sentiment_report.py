#!/usr/bin/env python3
"""
超短情绪报告生成模块 - P2 超短情绪分析

功能：
1. 生成 HTML 情绪报告（GitHub Pages 风格，与现有页面一致）
2. 生成微信推送摘要
3. 保存 JSON 结构化数据

用法：
    python3 multi_agent/sentiment_report.py          # 生成全部输出
    from sentiment_report import SentimentReportGenerator
    gen = SentimentReportGenerator()
    gen.run()   # 默认输出到 docs/
"""

import sys
import os
import json
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
REPO = BASE.parent

from sentiment_analyzer import (
    SentimentAnalyzer, SentimentResult,
    PHASE_ICE, PHASE_REPAIR, PHASE_START, PHASE_CLimax, PHASE_EBB
)

# 阶段配色（A股习惯：红=热/涨，绿=冷/跌 反向应用于情绪温度）
PHASE_STYLE = {
    PHASE_ICE:     {'emoji': '🧊', 'color': '#38bdf8', 'desc': '情绪出清，观望等待'},
    PHASE_REPAIR:  {'emoji': '🌱', 'color': '#4ade80', 'desc': '情绪回暖，小仓试错'},
    PHASE_START:   {'emoji': '🔥', 'color': '#f87171', 'desc': '情绪健康，正常执行'},
    PHASE_CLimax:  {'emoji': '⚠️', 'color': '#fbbf24', 'desc': '情绪过热，兑现为主'},
    PHASE_EBB:     {'emoji': '🌊', 'color': '#60a5fa', 'desc': '亏钱扩散，减仓防守'},
}


class SentimentReportGenerator:
    """超短情绪报告生成器"""

    def __init__(self, output_dir: str = None):
        self.output_dir = output_dir or str(REPO / 'docs')

    # ------------------------------------------------------------------
    # HTML 报告
    # ------------------------------------------------------------------
    def generate_html(self, result: SentimentResult, output_path: str = None) -> str:
        style = PHASE_STYLE.get(result.phase, PHASE_STYLE[PHASE_START])
        m = result.metrics

        # 历史序列数据（用于图表）
        hist_dates = json.dumps([h.date[5:] for h in result.history], ensure_ascii=False)
        hist_zt = json.dumps([h.zt_count for h in result.history])
        hist_premium = json.dumps([round(h.prev_avg_chg, 2) for h in result.history])

        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>超短情绪监控 - {result.date}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: #0f172a; color: #e2e8f0; line-height: 1.6; padding: 20px;
  }}
  .container {{ max-width: 960px; margin: 0 auto; }}
  h1 {{ color: #60a5fa; font-size: 24px; margin-bottom: 6px; }}
  .subtitle {{ color: #94a3b8; font-size: 13px; margin-bottom: 20px; }}

  .phase-banner {{
    background: linear-gradient(135deg, #1e293b, #334155);
    border-radius: 16px; padding: 24px; margin-bottom: 20px;
    display: flex; align-items: center; gap: 20px;
    border-left: 5px solid {style['color']};
  }}
  .phase-emoji {{ font-size: 52px; }}
  .phase-name {{ font-size: 22px; font-weight: 700; color: {style['color']}; }}
  .phase-desc {{ color: #94a3b8; font-size: 14px; }}
  .phase-conf {{ font-size: 12px; color: #64748b; margin-top: 4px; }}

  .metrics-grid {{
    display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
    gap: 12px; margin-bottom: 20px;
  }}
  .metric-card {{
    background: #1e293b; border-radius: 12px; padding: 14px; text-align: center;
  }}
  .metric-label {{ font-size: 12px; color: #94a3b8; margin-bottom: 6px; }}
  .metric-value {{ font-size: 22px; font-weight: 700; }}
  .metric-sub {{ font-size: 11px; color: #64748b; }}
  .up {{ color: #f87171; }}     /* A股习惯：红涨 */
  .down {{ color: #4ade80; }}   /* 绿跌 */

  .chart-box {{
    background: #1e293b; border-radius: 12px; padding: 16px; margin-bottom: 20px;
  }}
  .chart-title {{ font-size: 14px; color: #94a3b8; margin-bottom: 10px; }}

  .section {{ margin-bottom: 20px; }}
  .section-title {{
    font-size: 15px; font-weight: 600; color: #94a3b8;
    margin-bottom: 10px; display: flex; align-items: center; gap: 6px;
  }}
  .card {{
    background: #1e293b; border-radius: 12px; padding: 16px;
  }}
  .list-item {{ padding: 8px 0; border-bottom: 1px solid #334155; font-size: 14px; }}
  .list-item:last-child {{ border-bottom: none; }}

  .note {{ font-size: 12px; color: #64748b; margin-top: 16px; }}
  .footer {{
    text-align: center; color: #64748b; font-size: 12px;
    margin-top: 24px; padding-top: 14px; border-top: 1px solid #334155;
  }}
  @media (max-width: 600px) {{ .phase-banner {{ flex-direction: column; text-align: center; }} }}
</style>
</head>
<body>
<div class="container">
  <h1>📡 超短情绪监控</h1>
  <div class="subtitle">数据日期: {result.date} | 情绪周期: {result.phase}</div>

  <div class="phase-banner">
    <div class="phase-emoji">{style['emoji']}</div>
    <div>
      <div class="phase-name">{result.phase}</div>
      <div class="phase-desc">{style['desc']}</div>
      <div class="phase-conf">判定置信度 {result.phase_confidence:.0%}</div>
    </div>
  </div>

  <div class="metrics-grid">
    <div class="metric-card">
      <div class="metric-label">涨停</div>
      <div class="metric-value up">{m.zt_count}</div>
      <div class="metric-sub">首板 {m.first_board} / 2板+ {m.board_2plus}</div>
    </div>
    <div class="metric-card">
      <div class="metric-label">跌停</div>
      <div class="metric-value {'down' if m.dt_count > 5 else ''}">{m.dt_count}</div>
      <div class="metric-sub">恐慌盘</div>
    </div>
    <div class="metric-card">
      <div class="metric-label">炸板</div>
      <div class="metric-value">{m.zb_count}</div>
      <div class="metric-sub">封板率 {m.seal_rate:.0%}</div>
    </div>
    <div class="metric-card">
      <div class="metric-label">最高连板</div>
      <div class="metric-value {'up' if m.max_streak >= 5 else ''}">{m.max_streak}</div>
      <div class="metric-sub">空间高度</div>
    </div>
    <div class="metric-card">
      <div class="metric-label">昨日涨停溢价</div>
      <div class="metric-value {'up' if m.prev_avg_chg > 0 else 'down'}">{m.prev_avg_chg:+.2f}%</div>
      <div class="metric-sub">上涨占比 {m.prev_up_ratio:.0%}</div>
    </div>
  </div>

  <div class="chart-box">
    <div class="chart-title">📈 近10日情绪序列（涨停数 vs 昨日涨停溢价）</div>
    <canvas id="sentimentChart" height="120"></canvas>
  </div>
"""

        # 交易提示
        if result.signals:
            html += """
  <div class="section">
    <div class="section-title">💡 交易提示</div>
    <div class="card">
"""
            for s in result.signals:
                html += f'      <div class="list-item">{s}</div>\n'
            html += "    </div>\n  </div>\n"

        # 风险提示
        if result.risks:
            html += """
  <div class="section">
    <div class="section-title">⚠️ 风险提示</div>
    <div class="card">
"""
            for r in result.risks:
                html += f'      <div class="list-item">{r}</div>\n'
            html += "    </div>\n  </div>\n"

        # 分位数参考
        html += f"""
  <div class="section">
    <div class="section-title">📊 涨停数动态分位（60日滚动）</div>
    <div class="card">
      <div class="list-item">25分位: {m.zt_q25:.0f} | 中位数: {m.zt_q50:.0f} | 75分位: {m.zt_q75:.0f}</div>
      <div class="list-item">当前 {m.zt_count} 家处于 {'低位(<25%)' if m.zt_count < m.zt_q25 else '中位区间' if m.zt_count < m.zt_q75 else '高位(>75%)'}</div>
    </div>
  </div>
"""

        html += f"""
  <div class="note">📝 {result.indicators_note}</div>

  <div class="footer">
    生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}<br>
    数据源: akshare 东财涨停池/跌停池/炸板池 | 分析: LLM-native 情绪周期引擎 v1
  </div>
</div>

<script>
const ctx = document.getElementById('sentimentChart').getContext('2d');
new Chart(ctx, {{
  type: 'bar',
  data: {{
    labels: {hist_dates},
    datasets: [
      {{
        label: '涨停数',
        data: {hist_zt},
        backgroundColor: 'rgba(248,113,113,0.6)',
        borderColor: '#f87171',
        borderWidth: 1,
        yAxisID: 'y',
      }},
      {{
        label: '昨日涨停溢价%',
        data: {hist_premium},
        type: 'line',
        borderColor: '#60a5fa',
        backgroundColor: 'rgba(96,165,250,0.15)',
        tension: 0.3,
        yAxisID: 'y1',
      }}
    ]
  }},
  options: {{
    responsive: true,
    plugins: {{ legend: {{ labels: {{ color: '#94a3b8' }} }} }},
    scales: {{
      x: {{ ticks: {{ color: '#94a3b8' }}, grid: {{ color: '#334155' }} }},
      y: {{ position: 'left', ticks: {{ color: '#f87171' }}, grid: {{ color: '#334155' }}, title: {{ display: true, text: '涨停数', color: '#f87171' }} }},
      y1: {{ position: 'right', ticks: {{ color: '#60a5fa' }}, grid: {{ drawOnChartArea: false }}, title: {{ display: true, text: '溢价%', color: '#60a5fa' }} }}
    }}
  }}
}});
</script>
</body>
</html>
"""

        if output_path:
            os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(html)

        return html

    # ------------------------------------------------------------------
    # 微信摘要
    # ------------------------------------------------------------------
    def generate_wechat_summary(self, result: SentimentResult) -> str:
        style = PHASE_STYLE.get(result.phase, PHASE_STYLE[PHASE_START])
        m = result.metrics

        lines = []
        lines.append(f"📡 **超短情绪监控** {style['emoji']}")
        lines.append(f"📅 {result.date} | 周期: **{result.phase}** ({result.phase_confidence:.0%})")
        lines.append(f"_{style['desc']}_")
        lines.append("")
        lines.append(f"📊 涨停 **{m.zt_count}** | 跌停 {m.dt_count} | 炸板 {m.zb_count} (封板率{m.seal_rate:.0%})")
        lines.append(f"📊 最高 {m.max_streak} 连板 | 首板 {m.first_board} 家")
        lines.append(f"📊 昨日涨停今日 **{m.prev_avg_chg:+.2f}%** (上涨占比{m.prev_up_ratio:.0%})")
        lines.append("")
        for s in result.signals[:4]:
            lines.append(s)
        if result.risks:
            lines.append("")
            for r in result.risks[:2]:
                lines.append(f"⚠️ {r}")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # JSON 保存
    # ------------------------------------------------------------------
    def save_json(self, result: SentimentResult, output_path: str = None) -> str:
        if output_path is None:
            os.makedirs(os.path.join(self.output_dir, 'sentiment_data'), exist_ok=True)
            output_path = os.path.join(
                self.output_dir, 'sentiment_data',
                f"sentiment_{result.date.replace('-', '')}.json"
            )
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(result.to_dict(), f, ensure_ascii=False, indent=2)
        return output_path

    # ------------------------------------------------------------------
    # 一键执行
    # ------------------------------------------------------------------
    def run(self) -> dict:
        """抓取 → 分析 → 生成报告 → 保存，完整流水线"""
        from sentiment_fetcher import run_daily

        print("📥 步骤 1/3: 抓取今日情绪数据...")
        fetch_result = run_daily(verbose=True)

        print("\n🧠 步骤 2/3: 情绪周期分析...")
        result = SentimentAnalyzer().analyze()
        print(f"   ✅ {result.date} → {result.phase} (置信度 {result.phase_confidence:.0%})")

        print("\n📝 步骤 3/3: 生成报告...")
        html_path = os.path.join(self.output_dir, 'sentiment_report.html')
        self.generate_html(result, html_path)
        json_path = self.save_json(result)
        wechat = self.generate_wechat_summary(result)
        print(f"   ✅ HTML: {html_path}")
        print(f"   ✅ JSON: {json_path}")

        return {
            'result': result,
            'html_path': html_path,
            'json_path': json_path,
            'wechat_summary': wechat,
        }


if __name__ == '__main__':
    print("=" * 60)
    print("🧪 超短情绪报告生成测试")
    print("=" * 60)

    gen = SentimentReportGenerator()
    output = gen.run()

    print("\n📱 微信摘要:")
    print("-" * 60)
    print(output['wechat_summary'])
    print("-" * 60)
    print("\n✅ 全部完成")
