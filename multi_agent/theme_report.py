#!/usr/bin/env python3
"""
题材雷达报告生成模块 - P3 题材雷达

功能：
1. 生成 HTML 题材雷达报告（GitHub Pages 风格）
2. 生成微信推送摘要
3. 保存 JSON 结构化数据
4. 联动 P2 情绪周期

用法：
    python3 multi_agent/theme_report.py          # 生成全部输出
    from theme_report import ThemeReportGenerator
    gen = ThemeReportGenerator()
    gen.run()
"""

import sys
import os
import json
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
REPO = BASE.parent

from theme_radar import ThemeRadar, ThemeRadarResult
from sentiment_analyzer import PHASE_EBB, PHASE_CLimax, PHASE_ICE, PHASE_REPAIR, PHASE_START

# 阶段配色
PHASE_STYLE = {
    PHASE_ICE:     {'emoji': '🧊', 'color': '#38bdf8'},
    PHASE_REPAIR:  {'emoji': '🌱', 'color': '#4ade80'},
    PHASE_START:   {'emoji': '🔥', 'color': '#f87171'},
    PHASE_CLimax:  {'emoji': '⚠️', 'color': '#fbbf24'},
    PHASE_EBB:     {'emoji': '🌊', 'color': '#60a5fa'},
}


class ThemeReportGenerator:
    """题材雷达报告生成器"""

    def __init__(self, output_dir: str = None):
        self.output_dir = output_dir or str(REPO / 'docs')

    # ------------------------------------------------------------------
    # HTML 报告
    # ------------------------------------------------------------------
    def generate_html(self, result: ThemeRadarResult, output_path: str = None) -> str:
        style = PHASE_STYLE.get(result.phase, PHASE_STYLE[PHASE_START])

        # 题材强度条颜色
        def strength_color(s):
            if s >= 7: return '#f87171'
            if s >= 5: return '#fbbf24'
            return '#60a5fa'

        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>题材雷达 - {result.date}</title>
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
    border-radius: 16px; padding: 20px; margin-bottom: 20px;
    display: flex; align-items: center; gap: 16px;
    border-left: 5px solid {style['color']};
  }}
  .phase-emoji {{ font-size: 48px; }}
  .phase-name {{ font-size: 20px; font-weight: 700; color: {style['color']}; }}
  .phase-desc {{ color: #94a3b8; font-size: 14px; }}

  .summary {{
    background: #1e293b; border-radius: 12px; padding: 16px; margin-bottom: 20px;
    font-size: 15px; color: #e2e8f0;
  }}

  .section {{ margin-bottom: 20px; }}
  .section-title {{
    font-size: 15px; font-weight: 600; color: #94a3b8;
    margin-bottom: 12px; display: flex; align-items: center; gap: 6px;
  }}

  .theme-grid {{
    display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
    gap: 12px;
  }}
  .theme-card {{
    background: #1e293b; border-radius: 12px; padding: 16px;
    border-left: 4px solid #60a5fa;
  }}
  .theme-name {{ font-size: 16px; font-weight: 700; color: #e2e8f0; margin-bottom: 8px; }}
  .theme-stats {{ display: flex; gap: 12px; font-size: 12px; color: #94a3b8; margin-bottom: 10px; }}
  .theme-stat {{ text-align: center; }}
  .theme-stat-val {{ font-size: 18px; font-weight: 700; color: #e2e8f0; }}
  .theme-strength {{
    height: 6px; background: #334155; border-radius: 3px; overflow: hidden;
  }}
  .theme-strength-bar {{
    height: 100%; border-radius: 3px; transition: width 0.3s;
  }}
  .strength-label {{ font-size: 11px; color: #64748b; margin-top: 4px; text-align: right; }}

  .stock-list {{ font-size: 12px; color: #94a3b8; margin-top: 8px; }}
  .stock-item {{
    display: flex; justify-content: space-between; padding: 4px 0;
    border-bottom: 1px solid #334155;
  }}
  .stock-item:last-child {{ border-bottom: none; }}
  .board-badge {{
    display: inline-block; padding: 1px 6px; border-radius: 4px;
    font-size: 11px; font-weight: 600;
  }}
  .board-high {{ background: rgba(248,113,113,0.2); color: #f87171; }}
  .board-mid {{ background: rgba(251,191,36,0.2); color: #fbbf24; }}
  .board-low {{ background: rgba(96,165,250,0.2); color: #60a5fa; }}

  .list-box {{
    background: #1e293b; border-radius: 12px; padding: 14px;
  }}
  .list-item {{ padding: 6px 0; border-bottom: 1px solid #334155; font-size: 14px; }}
  .list-item:last-child {{ border-bottom: none; }}
  .avoid-item {{ color: #f87171; }}
  .watch-item {{ color: #fbbf24; }}

  .footer {{
    text-align: center; color: #64748b; font-size: 12px;
    margin-top: 24px; padding-top: 14px; border-top: 1px solid #334155;
  }}
</style>
</head>
<body>
<div class="container">
  <h1>📡 题材雷达</h1>
  <div class="subtitle">数据日期: {result.date} | 情绪周期: {result.phase}</div>

  <div class="phase-banner">
    <div class="phase-emoji">{style['emoji']}</div>
    <div>
      <div class="phase-name">{result.phase}</div>
      <div class="phase-desc">{result.market_summary}</div>
    </div>
  </div>
"""

        # 活跃题材
        if result.themes:
            html += f"""
  <div class="section">
    <div class="section-title">🔥 活跃题材（{len(result.themes)}个）</div>
    <div class="theme-grid">
"""
            for t in result.themes:
                # 计算强度分（从to_dict里拿）
                t_dict = t.to_dict()
                strength = t_dict.get('strength', 5)
                color = strength_color(strength)
                html += f"""
      <div class="theme-card" style="border-left-color: {color}">
        <div class="theme-name">{t.name}</div>
        <div class="theme-stats">
          <div class="theme-stat">
            <div class="theme-stat-val">{t.zt_count}</div>
            <div>涨停</div>
          </div>
          <div class="theme-stat">
            <div class="theme-stat-val">{t.max_board}</div>
            <div>最高板</div>
          </div>
          <div class="theme-stat">
            <div class="theme-stat-val">{t.first_board}</div>
            <div>首板</div>
          </div>
          <div class="theme-stat">
            <div class="theme-stat-val">{t.avg_fund:.0f}万</div>
            <div>封单均值</div>
          </div>
        </div>
        <div class="theme-strength">
          <div class="theme-strength-bar" style="width: {min(strength*10, 100)}%; background: {color}"></div>
        </div>
        <div class="strength-label">强度 {strength:.1f}/10</div>
        <div class="stock-list">
"""
                for s in t.stocks[:3]:
                    badge_class = 'board-high' if s['board'] >= 3 else 'board-mid' if s['board'] == 2 else 'board-low'
                    html += f"""
          <div class="stock-item">
            <span>{s['ticker']}</span>
            <span class="board-badge {badge_class}">{s['board']}板</span>
          </div>
"""
                html += "        </div>\n      </div>\n"
            html += "    </div>\n  </div>\n"

        # 观察名单
        if result.watchlist:
            html += f"""
  <div class="section">
    <div class="section-title">👀 观察名单</div>
    <div class="list-box">
"""
            for name in result.watchlist:
                html += f'      <div class="list-item watch-item">⚡ {name}</div>\n'
            html += "    </div>\n  </div>\n"

        # 回避名单
        if result.avoid:
            html += f"""
  <div class="section">
    <div class="section-title">🚫 回避名单（{result.phase}期）</div>
    <div class="list-box">
"""
            for name in result.avoid:
                html += f'      <div class="list-item avoid-item">❌ {name}</div>\n'
            html += "    </div>\n  </div>\n"

        html += f"""
  <div class="footer">
    生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}<br>
    数据源: akshare 东财涨停池 | 分析: LLM-native 题材雷达 v1
  </div>
</div>
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
    def generate_wechat_summary(self, result: ThemeRadarResult) -> str:
        style = PHASE_STYLE.get(result.phase, PHASE_STYLE[PHASE_START])
        lines = []
        lines.append(f"📡 **题材雷达** {style['emoji']}")
        lines.append(f"📅 {result.date} | {result.phase}")
        lines.append("")
        lines.append(result.market_summary)
        lines.append("")

        if result.themes:
            lines.append("🔥 **活跃题材:**")
            for t in result.themes[:3]:
                lines.append(f"  【{t.name}】{t.zt_count}只涨停 最高{t.max_board}板 封单均值{t.avg_fund:.0f}万")

        if result.avoid:
            lines.append("")
            lines.append(f"🚫 **回避:** {', '.join(result.avoid)}")

        if result.watchlist:
            lines.append(f"👀 **观察:** {', '.join(result.watchlist)}")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # JSON 保存
    # ------------------------------------------------------------------
    def save_json(self, result: ThemeRadarResult, output_path: str = None) -> str:
        if output_path is None:
            os.makedirs(os.path.join(self.output_dir, 'sentiment_data'), exist_ok=True)
            output_path = os.path.join(
                self.output_dir, 'sentiment_data',
                f"themes_{result.date.replace('-', '')}.json"
            )
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(result.to_dict(), f, ensure_ascii=False, indent=2)
        return output_path

    # ------------------------------------------------------------------
    # 一键执行
    # ------------------------------------------------------------------
    def run(self) -> dict:
        """分析 → 生成报告 → 保存，完整流水线"""
        print("🧠 步骤 1/2: 题材雷达分析...")
        result = ThemeRadar().analyze()
        print(f"   ✅ {result.date} → {len(result.themes)}个活跃题材")

        print("\n📝 步骤 2/2: 生成报告...")
        html_path = os.path.join(self.output_dir, 'theme_report.html')
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
    print("🧪 题材雷达报告生成测试")
    print("=" * 60)

    gen = ThemeReportGenerator()
    output = gen.run()

    print("\n📱 微信摘要:")
    print("-" * 60)
    print(output['wechat_summary'])
    print("-" * 60)
    print("\n✅ 全部完成")
