#!/usr/bin/env python3
"""
观点共识报告生成模块 - P1 大V复盘

功能：
1. 生成 HTML 格式的观点共识报告
2. 生成微信推送摘要
3. 支持保存到 GitHub Pages

用法：
    from narrative_report import NarrativeReportGenerator
    
    generator = NarrativeReportGenerator()
    html = generator.generate_html(narrative_result)
    wechat = generator.generate_wechat_summary(narrative_result)
"""

import json
import os
from datetime import datetime
from typing import List, Dict, Optional

from narrative_analyzer import NarrativeResult, TopicConsensus


class NarrativeReportGenerator:
    """观点共识报告生成器"""
    
    def __init__(self, output_dir: str = None):
        self.output_dir = output_dir or os.path.join(
            '/home/liudawei/github/daily_tracker_analytics',
            'docs'
        )
    
    def generate_html(
        self,
        result: NarrativeResult,
        output_path: str = None
    ) -> str:
        """
        生成 HTML 报告
        
        Args:
            result: 观点分析结果
            output_path: 输出路径（可选）
            
        Returns:
            HTML 内容
        """
        # 整体情绪样式
        sentiment_config = {
            'bullish': {'emoji': '🟢', 'color': '#22c55e', 'label': '看多'},
            'bearish': {'emoji': '🔴', 'color': '#ef4444', 'label': '看空'},
            'neutral': {'emoji': '🟡', 'color': '#eab308', 'label': '中性'}
        }
        sentiment = sentiment_config.get(
            result.overall_sentiment,
            sentiment_config['neutral']
        )
        
        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>市场观点共识报告 - {result.analysis_date}</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ 
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", sans-serif;
    background: #0f172a; 
    color: #e2e8f0; 
    line-height: 1.6;
    padding: 20px;
  }}
  .container {{ max-width: 900px; margin: 0 auto; }}
  h1 {{ color: #60a5fa; font-size: 24px; margin-bottom: 8px; }}
  .subtitle {{ color: #94a3b8; font-size: 14px; margin-bottom: 24px; }}
  
  .overview {{
    background: linear-gradient(135deg, #1e293b 0%, #334155 100%);
    border-radius: 16px;
    padding: 24px;
    margin-bottom: 24px;
    display: flex;
    align-items: center;
    gap: 24px;
  }}
  .sentiment-badge {{
    width: 80px; height: 80px;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 40px;
    background: {sentiment['color']}22;
    border: 3px solid {sentiment['color']};
  }}
  .overview-info {{ flex: 1; }}
  .overview-title {{ font-size: 18px; font-weight: 600; margin-bottom: 4px; }}
  .overview-meta {{ color: #94a3b8; font-size: 13px; }}
  .confidence-bar {{
    width: 100%; height: 8px;
    background: #334155;
    border-radius: 4px;
    margin-top: 12px;
    overflow: hidden;
  }}
  .confidence-fill {{
    height: 100%;
    width: {result.overall_confidence * 100:.0f}%;
    background: {sentiment['color']};
    border-radius: 4px;
  }}
  
  .section {{ margin-bottom: 24px; }}
  .section-title {{
    font-size: 16px;
    font-weight: 600;
    color: #94a3b8;
    margin-bottom: 12px;
    display: flex;
    align-items: center;
    gap: 8px;
  }}
  
  .topic-card {{
    background: #1e293b;
    border-radius: 12px;
    padding: 16px;
    margin-bottom: 12px;
    border-left: 4px solid #334155;
  }}
  .topic-card.bullish {{ border-left-color: #22c55e; }}
  .topic-card.bearish {{ border-left-color: #ef4444; }}
  .topic-card.neutral {{ border-left-color: #eab308; }}
  
  .topic-header {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 12px;
  }}
  .topic-name {{ font-size: 16px; font-weight: 600; }}
  .topic-stance {{
    font-size: 12px;
    padding: 4px 12px;
    border-radius: 12px;
  }}
  .stance-bullish {{ background: #22c55e22; color: #4ade80; }}
  .stance-bearish {{ background: #ef444422; color: #f87171; }}
  .stance-neutral {{ background: #eab30822; color: #facc15; }}
  
  .topic-stats {{
    display: flex;
    gap: 16px;
    font-size: 13px;
    color: #94a3b8;
    margin-bottom: 12px;
  }}
  .stat-item {{ display: flex; align-items: center; gap: 4px; }}
  
  .topic-points {{
    background: #0f172a;
    border-radius: 8px;
    padding: 12px;
    font-size: 13px;
  }}
  .topic-points li {{
    margin: 4px 0;
    padding-left: 16px;
    position: relative;
  }}
  .topic-points li::before {{
    content: "•";
    position: absolute;
    left: 4px;
    color: #64748b;
  }}
  
  .insights-list {{
    background: #1e293b;
    border-radius: 12px;
    padding: 16px;
  }}
  .insights-list li {{
    padding: 8px 0;
    border-bottom: 1px solid #334155;
  }}
  .insights-list li:last-child {{ border-bottom: none; }}
  
  .risks-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
    gap: 12px;
  }}
  .risk-card {{
    background: #1e293b;
    border-radius: 12px;
    padding: 16px;
    border-left: 4px solid #f59e0b;
  }}
  
  .watch-list {{
    background: #1e293b;
    border-radius: 12px;
    padding: 16px;
  }}
  .watch-item {{
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px 0;
    font-size: 14px;
  }}
  .watch-icon {{ color: #60a5fa; }}
  
  .footer {{
    text-align: center;
    color: #64748b;
    font-size: 12px;
    margin-top: 32px;
    padding-top: 16px;
    border-top: 1px solid #334155;
  }}
  
  @media (max-width: 600px) {{
    .overview {{ flex-direction: column; text-align: center; }}
    .risks-grid {{ grid-template-columns: 1fr; }}
  }}
</style>
</head>
<body>
<div class="container">
  <h1>📊 市场观点共识报告</h1>
  <div class="subtitle">分析日期: {result.analysis_date} | 新闻来源: {result.total_news} 条</div>
  
  <div class="overview">
    <div class="sentiment-badge">{sentiment['emoji']}</div>
    <div class="overview-info">
      <div class="overview-title">整体情绪: {sentiment['label']}</div>
      <div class="overview-meta">
        置信度 {result.overall_confidence:.0%} | 
        覆盖话题 {len(result.topics)} 个
      </div>
      <div class="confidence-bar">
        <div class="confidence-fill"></div>
      </div>
    </div>
  </div>
"""
        
        # 话题共识
        if result.topics:
            html += """
  <div class="section">
    <div class="section-title">📈 话题共识</div>
"""
            for topic in result.topics[:8]:
                stance_class = f"stance-{topic.consensus_stance}"
                card_class = topic.consensus_stance
                stance_label = {
                    'bullish': '看多',
                    'bearish': '看空',
                    'neutral': '中性'
                }.get(topic.consensus_stance, '中性')
                
                html += f"""
    <div class="topic-card {card_class}">
      <div class="topic-header">
        <span class="topic-name">{topic.topic}</span>
        <span class="topic-stance {stance_class}">{stance_label} {topic.consensus_strength:.0%}</span>
      </div>
      <div class="topic-stats">
        <span class="stat-item">📰 提及 {topic.total_mentions}</span>
        <span class="stat-item">🟢 看多 {topic.bullish_count}</span>
        <span class="stat-item">🔴 看空 {topic.bearish_count}</span>
      </div>
"""
                if topic.key_points:
                    html += """
      <ul class="topic-points">
"""
                    for point in topic.key_points[:3]:
                        html += f"        <li>{point[:80]}...</li>\n"
                    html += "      </ul>\n"
                
                html += "    </div>\n"
            
            html += "  </div>\n"
        
        # 关键洞察
        if result.key_insights:
            html += """
  <div class="section">
    <div class="section-title">💡 关键洞察</div>
    <ul class="insights-list">
"""
            for insight in result.key_insights:
                html += f"      <li>{insight}</li>\n"
            html += """    </ul>
  </div>
"""
        
        # 风险提示
        if result.risks:
            html += """
  <div class="section">
    <div class="section-title">⚠️ 风险提示</div>
    <div class="risks-grid">
"""
            for risk in result.risks[:4]:
                html += f"""
      <div class="risk-card">{risk[:100]}...</div>
"""
            html += """    </div>
  </div>
"""
        
        # 关注事项
        if result.watch_items:
            html += """
  <div class="section">
    <div class="section-title">👀 关注事项</div>
    <div class="watch-list">
"""
            for item in result.watch_items:
                html += f"""
      <div class="watch-item">
        <span class="watch-icon">▸</span>
        <span>{item}</span>
      </div>
"""
            html += """    </div>
  </div>
"""
        
        html += f"""
  <div class="footer">
    报告生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}<br>
    数据来源: 东方财富 / 新浪财经 | 分析: LLM-native 观点提炼引擎
  </div>
</div>
</body>
</html>
"""
        
        # 保存到文件
        if output_path:
            os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(html)
        
        return html
    
    def generate_wechat_summary(self, result: NarrativeResult) -> str:
        """
        生成微信推送摘要
        
        Args:
            result: 观点分析结果
            
        Returns:
            微信格式的摘要文本
        """
        lines = []
        
        # 标题
        lines.append("📊 **市场观点共识**")
        lines.append(f"📅 {result.analysis_date}")
        lines.append("")
        
        # 整体情绪
        sentiment_emoji = {
            'bullish': '🟢',
            'bearish': '🔴',
            'neutral': '🟡'
        }.get(result.overall_sentiment, '⚪')
        
        lines.append(f"{sentiment_emoji} 整体: {result.overall_sentiment} "
                    f"(置信度 {result.overall_confidence:.0%})")
        lines.append("")
        
        # 话题共识（前5个）
        if result.topics:
            lines.append("📈 **话题共识**")
            for topic in result.topics[:5]:
                stance_emoji = {
                    'bullish': '🟢',
                    'bearish': '🔴',
                    'neutral': '🟡'
                }.get(topic.consensus_stance, '⚪')
                
                lines.append(
                    f"{stance_emoji} {topic.topic}: {topic.consensus_stance} "
                    f"({topic.total_mentions}次提及)"
                )
            lines.append("")
        
        # 关键洞察（前3个）
        if result.key_insights:
            lines.append("💡 **关键洞察**")
            for insight in result.key_insights[:3]:
                lines.append(f"  {insight}")
            lines.append("")
        
        # 风险提示（前2个）
        if result.risks:
            lines.append("⚠️ **风险提示**")
            for risk in result.risks[:2]:
                short_risk = risk[:40] + "..." if len(risk) > 40 else risk
                lines.append(f"  {short_risk}")
            lines.append("")
        
        # 关注事项（前3个）
        if result.watch_items:
            lines.append("👀 **关注事项**")
            for item in result.watch_items[:3]:
                lines.append(f"  ▸ {item}")
        
        return "\n".join(lines)
    
    def save_json(
        self,
        result: NarrativeResult,
        output_path: str = None
    ) -> str:
        """
        保存结果为 JSON
        
        Args:
            result: 观点分析结果
            output_path: 输出路径（可选）
            
        Returns:
            保存的文件路径
        """
        if output_path is None:
            output_path = os.path.join(
                self.output_dir,
                'narrative_data',
                f"narrative_{result.analysis_date.replace('-', '')}.json"
            )
        
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(result.to_dict(), f, ensure_ascii=False, indent=2)
        
        return output_path


# ============================================================
# 完整流程示例
# ============================================================

def run_full_pipeline(
    keywords: List[str] = None,
    limit_per_source: int = 20,
    output_dir: str = None
) -> Dict:
    """
    运行完整的大V复盘流程
    
    Args:
        keywords: 关键词过滤
        limit_per_source: 每个源的数量限制
        output_dir: 输出目录
        
    Returns:
        包含所有输出的字典
    """
    from news_fetcher import NewsFetcher
    from narrative_analyzer import NarrativeAnalyzer
    
    # 1. 抓取新闻
    print("📰 步骤 1/4: 抓取新闻...")
    fetcher = NewsFetcher()
    news_items = fetcher.fetch_all(
        keywords=keywords,
        limit_per_source=limit_per_source
    )
    print(f"   ✅ 获取到 {len(news_items)} 条新闻")
    
    # 2. 分析观点
    print("\n🧠 步骤 2/4: 分析观点...")
    analyzer = NarrativeAnalyzer()
    result = analyzer.analyze(news_items)
    print(f"   ✅ 识别 {len(result.topics)} 个话题")
    
    # 3. 生成报告
    print("\n📝 步骤 3/4: 生成报告...")
    generator = NarrativeReportGenerator(output_dir)
    
    # HTML 报告
    html_path = os.path.join(
        output_dir or generator.output_dir,
        'narrative_report.html'
    )
    html = generator.generate_html(result, html_path)
    print(f"   ✅ HTML 报告: {html_path}")
    
    # JSON 数据
    json_path = generator.save_json(result)
    print(f"   ✅ JSON 数据: {json_path}")
    
    # 4. 微信摘要
    print("\n📱 步骤 4/4: 生成微信摘要...")
    wechat = generator.generate_wechat_summary(result)
    print("   ✅ 微信摘要已生成")
    
    return {
        'result': result,
        'html': html,
        'html_path': html_path,
        'json_path': json_path,
        'wechat_summary': wechat
    }


# ============================================================
# 测试
# ============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("🧪 观点共识报告生成模块测试")
    print("=" * 60)
    
    from narrative_analyzer import NarrativeAnalyzer, NarrativeResult, TopicConsensus
    
    # 创建模拟结果
    mock_result = NarrativeResult(
        analysis_date="2026-09-23",
        total_news=25,
        overall_sentiment="bullish",
        overall_confidence=0.72,
        topics=[
            TopicConsensus(
                topic="AI/算力",
                total_mentions=8,
                bullish_count=6,
                bearish_count=1,
                neutral_count=1,
                consensus_stance="bullish",
                consensus_strength=0.75,
                key_points=[
                    "AI服务器订单饱满，厂商满产",
                    "算力租赁价格维持高位",
                    "国产芯片替代加速"
                ],
                risks=["估值偏高，注意回调风险"]
            ),
            TopicConsensus(
                topic="半导体",
                total_mentions=6,
                bullish_count=4,
                bearish_count=2,
                neutral_count=0,
                consensus_stance="bullish",
                consensus_strength=0.67,
                key_points=[
                    "光刻机国产化取得突破",
                    "存储芯片涨价周期开启"
                ],
                risks=["地缘政治风险"]
            ),
            TopicConsensus(
                topic="消费",
                total_mentions=5,
                bullish_count=1,
                bearish_count=4,
                neutral_count=0,
                consensus_stance="bearish",
                consensus_strength=0.80,
                key_points=[
                    "白酒库存压力仍存",
                    "可选消费复苏乏力"
                ],
                risks=["消费信心不足"]
            ),
        ],
        key_insights=[
            "🟢 AI/算力: bullish (强度 75%, 提及 8 次)",
            "🟢 半导体: bullish (强度 67%, 提及 6 次)",
            "🔴 消费: bearish (强度 80%, 提及 5 次)"
        ],
        risks=[
            "AI 板块估值偏高，短期注意回调风险",
            "地缘政治影响半导体供应链",
            "消费复苏不及预期"
        ],
        watch_items=[
            "关注 AI/算力 的持续性，防止过度拥挤",
            "关注 消费 是否出现反转信号"
        ]
    )
    
    # 测试 HTML 生成
    print("\n【测试1】生成 HTML 报告")
    generator = NarrativeReportGenerator()
    html = generator.generate_html(mock_result, '/tmp/test_narrative.html')
    print(f"✅ HTML 报告已生成 ({len(html)} bytes)")
    
    # 测试微信摘要
    print("\n【测试2】生成微信摘要")
    wechat = generator.generate_wechat_summary(mock_result)
    print(wechat)
    
    # 测试 JSON 保存
    print("\n【测试3】保存 JSON")
    json_path = generator.save_json(mock_result, '/tmp/test_narrative.json')
    print(f"✅ JSON 已保存: {json_path}")
    
    print("\n" + "=" * 60)
    print("✅ 测试完成")
