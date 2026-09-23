#!/usr/bin/env python3
"""
观点提炼分析模块 - P1 大V复盘

功能：
1. 从新闻聚合中提取市场观点
2. AI 提炼共识/分歧/风险
3. 生成结构化的观点报告数据

借鉴 easy-stock 的 narrative.go 设计：
- 概念标签归一化
- 结构化叙事识别
- 观点共识提取

用法：
    from narrative_analyzer import NarrativeAnalyzer
    
    analyzer = NarrativeAnalyzer()
    result = analyzer.analyze(news_items, keyword_groups)
    # 返回观点分析结果
"""

import json
import re
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field, asdict
from collections import defaultdict

from news_fetcher import NewsItem, KEYWORD_GROUPS


@dataclass
class Viewpoint:
    """
    单条观点
    
    Attributes:
        topic: 话题（如 "AI/算力"）
        stance: 立场（bullish/bearish/neutral）
        content: 观点内容
        source_ids: 来源新闻 ID 列表
        confidence: 置信度（0-1）
        keywords: 相关关键词
    """
    topic: str
    stance: str  # bullish / bearish / neutral
    content: str
    source_ids: List[str] = field(default_factory=list)
    confidence: float = 0.5
    keywords: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class TopicConsensus:
    """
    话题共识
    
    Attributes:
        topic: 话题名称
        total_mentions: 总提及次数
        bullish_count: 看多数量
        bearish_count: 看空数量
        neutral_count: 中性数量
        consensus_stance: 共识立场
        consensus_strength: 共识强度（0-1）
        key_points: 关键观点列表
        risks: 风险点列表
    """
    topic: str
    total_mentions: int = 0
    bullish_count: int = 0
    bearish_count: int = 0
    neutral_count: int = 0
    consensus_stance: str = "neutral"
    consensus_strength: float = 0.0
    key_points: List[str] = field(default_factory=list)
    risks: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class NarrativeResult:
    """
    观点分析结果
    
    Attributes:
        analysis_date: 分析日期
        total_news: 新闻总数
        topics: 话题共识列表
        overall_sentiment: 整体情绪（bullish/bearish/neutral）
        overall_confidence: 整体置信度
        key_insights: 关键洞察列表
        risks: 风险列表
        watch_items: 关注事项列表
    """
    analysis_date: str
    total_news: int = 0
    topics: List[TopicConsensus] = field(default_factory=list)
    overall_sentiment: str = "neutral"
    overall_confidence: float = 0.5
    key_insights: List[str] = field(default_factory=list)
    risks: List[str] = field(default_factory=list)
    watch_items: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict:
        return {
            'analysis_date': self.analysis_date,
            'total_news': self.total_news,
            'topics': [t.to_dict() for t in self.topics],
            'overall_sentiment': self.overall_sentiment,
            'overall_confidence': self.overall_confidence,
            'key_insights': self.key_insights,
            'risks': self.risks,
            'watch_items': self.watch_items
        }


class NarrativeAnalyzer:
    """
    观点提炼分析器
    
    从新闻聚合中提取市场观点，识别共识和分歧
    """
    
    # 看涨关键词
    BULLISH_KEYWORDS = [
        '上涨', '看涨', '看好', '买入', '增持', '推荐', '目标价上调',
        '突破', '创新高', '涨停', '反弹', '复苏', '回暖', '改善',
        '增长', '超预期', '利好', '受益', '景气', '扩张', '订单饱满',
        '供不应求', '涨价', '提价', '满产', '排产', '产能利用率'
    ]
    
    # 看跌关键词
    BEARISH_KEYWORDS = [
        '下跌', '看跌', '看空', '卖出', '减持', '回避', '目标价下调',
        '跌破', '创新低', '跌停', '回调', '下滑', '衰退', '恶化',
        '下降', '不及预期', '利空', '受损', '低迷', '收缩', '订单下滑',
        '供过于求', '降价', '库存', '产能过剩', '亏损'
    ]
    
    # 风险关键词
    RISK_KEYWORDS = [
        '风险', '警惕', '注意', '谨慎', '关注', '不确定', '波动',
        '回调压力', '估值偏高', '泡沫', '监管', '政策风险', '地缘',
        '汇率', '利率', '流动性', '信用', '违约', '减持', '解禁'
    ]
    
    def __init__(self):
        self.keyword_groups = KEYWORD_GROUPS
    
    def analyze(
        self,
        news_items: List[NewsItem],
        custom_groups: Dict[str, List[str]] = None
    ) -> NarrativeResult:
        """
        分析新闻聚合，提取观点
        
        Args:
            news_items: 新闻列表
            custom_groups: 自定义关键词分组（可选）
            
        Returns:
            NarrativeResult
        """
        groups = custom_groups or self.keyword_groups
        
        # 初始化结果
        result = NarrativeResult(
            analysis_date=datetime.now().strftime('%Y-%m-%d'),
            total_news=len(news_items)
        )
        
        # 按话题分组
        topic_news = self._group_by_topics(news_items, groups)
        
        # 分析每个话题
        for topic, items in topic_news.items():
            consensus = self._analyze_topic(topic, items)
            result.topics.append(consensus)
        
        # 按提及次数排序
        result.topics.sort(key=lambda x: x.total_mentions, reverse=True)
        
        # 计算整体情绪
        result.overall_sentiment, result.overall_confidence = \
            self._calculate_overall_sentiment(result.topics)
        
        # 提取关键洞察
        result.key_insights = self._extract_key_insights(result.topics)
        
        # 提取风险
        result.risks = self._extract_risks(news_items)
        
        # 生成关注事项
        result.watch_items = self._generate_watch_items(result.topics)
        
        return result
    
    def _group_by_topics(
        self,
        news_items: List[NewsItem],
        groups: Dict[str, List[str]]
    ) -> Dict[str, List[NewsItem]]:
        """按话题分组新闻"""
        topic_news = defaultdict(list)
        
        for item in news_items:
            text = (item.title + " " + item.content).lower()
            
            for topic, keywords in groups.items():
                for kw in keywords:
                    if kw.lower() in text:
                        topic_news[topic].append(item)
                        break  # 一个新闻只属于一个话题
        
        return topic_news
    
    def _analyze_topic(self, topic: str, items: List[NewsItem]) -> TopicConsensus:
        """分析单个话题"""
        consensus = TopicConsensus(topic=topic)
        consensus.total_mentions = len(items)
        
        key_points = []
        risks = []
        
        for item in items:
            text = item.title + " " + item.content
            
            # 判断立场
            stance = self._detect_stance(text)
            
            if stance == "bullish":
                consensus.bullish_count += 1
            elif stance == "bearish":
                consensus.bearish_count += 1
            else:
                consensus.neutral_count += 1
            
            # 提取关键观点
            points = self._extract_points(text)
            key_points.extend(points)
            
            # 提取风险
            topic_risks = self._extract_topic_risks(text)
            risks.extend(topic_risks)
        
        # 计算共识立场
        if consensus.bullish_count > consensus.bearish_count * 1.5:
            consensus.consensus_stance = "bullish"
            consensus.consensus_strength = min(
                0.9, consensus.bullish_count / max(consensus.total_mentions, 1)
            )
        elif consensus.bearish_count > consensus.bullish_count * 1.5:
            consensus.consensus_stance = "bearish"
            consensus.consensus_strength = min(
                0.9, consensus.bearish_count / max(consensus.total_mentions, 1)
            )
        else:
            consensus.consensus_stance = "neutral"
            consensus.consensus_strength = 0.5
        
        # 去重并限制数量
        consensus.key_points = list(dict.fromkeys(key_points))[:5]
        consensus.risks = list(dict.fromkeys(risks))[:3]
        
        return consensus
    
    def _detect_stance(self, text: str) -> str:
        """检测文本立场"""
        text_lower = text.lower()
        
        bullish_score = sum(1 for kw in self.BULLISH_KEYWORDS if kw in text_lower)
        bearish_score = sum(1 for kw in self.BEARISH_KEYWORDS if kw in text_lower)
        
        if bullish_score > bearish_score * 1.5:
            return "bullish"
        elif bearish_score > bullish_score * 1.5:
            return "bearish"
        else:
            return "neutral"
    
    def _extract_points(self, text: str) -> List[str]:
        """提取关键观点"""
        points = []
        
        # 提取包含关键词的句子
        sentences = re.split(r'[。！？；]', text)
        for sent in sentences:
            sent = sent.strip()
            if len(sent) < 10:
                continue
            
            # 检查是否包含看涨/看跌关键词
            for kw in self.BULLISH_KEYWORDS + self.BEARISH_KEYWORDS:
                if kw in sent:
                    points.append(sent[:100])
                    break
        
        return points[:3]  # 最多3个观点
    
    def _extract_topic_risks(self, text: str) -> List[str]:
        """提取话题相关风险"""
        risks = []
        
        sentences = re.split(r'[。！？；]', text)
        for sent in sentences:
            sent = sent.strip()
            if len(sent) < 10:
                continue
            
            for kw in self.RISK_KEYWORDS:
                if kw in sent:
                    risks.append(sent[:100])
                    break
        
        return risks[:2]
    
    def _extract_risks(self, news_items: List[NewsItem]) -> List[str]:
        """从所有新闻中提取风险"""
        all_risks = []
        
        for item in news_items:
            risks = self._extract_topic_risks(item.title + " " + item.content)
            all_risks.extend(risks)
        
        # 去重并排序
        unique_risks = list(dict.fromkeys(all_risks))
        return unique_risks[:5]
    
    def _calculate_overall_sentiment(
        self,
        topics: List[TopicConsensus]
    ) -> Tuple[str, float]:
        """计算整体情绪"""
        if not topics:
            return "neutral", 0.5
        
        total_weight = sum(t.total_mentions for t in topics)
        if total_weight == 0:
            return "neutral", 0.5
        
        weighted_sentiment = sum(
            t.total_mentions * (
                1 if t.consensus_stance == "bullish" else
                -1 if t.consensus_stance == "bearish" else 0
            ) * t.consensus_strength
            for t in topics
        ) / total_weight
        
        avg_strength = sum(t.consensus_strength for t in topics) / len(topics)
        
        if weighted_sentiment > 0.3:
            return "bullish", min(0.9, avg_strength)
        elif weighted_sentiment < -0.3:
            return "bearish", min(0.9, avg_strength)
        else:
            return "neutral", avg_strength
    
    def _extract_key_insights(self, topics: List[TopicConsensus]) -> List[str]:
        """提取关键洞察"""
        insights = []
        
        # 找出共识最强的话题
        strong_topics = [
            t for t in topics
            if t.consensus_strength > 0.6 and t.total_mentions >= 2
        ]
        
        for t in strong_topics[:3]:
            stance_emoji = {
                "bullish": "🟢",
                "bearish": "🔴",
                "neutral": "🟡"
            }.get(t.consensus_stance, "⚪")
            
            insight = f"{stance_emoji} {t.topic}: {t.consensus_stance} (强度 {t.consensus_strength:.0%}, 提及 {t.total_mentions} 次)"
            insights.append(insight)
        
        # 找出分歧最大的话题
        divergent_topics = [
            t for t in topics
            if t.bullish_count > 0 and t.bearish_count > 0
            and abs(t.bullish_count - t.bearish_count) <= 1
            and t.total_mentions >= 3
        ]
        
        for t in divergent_topics[:2]:
            insight = f"⚡ {t.topic}: 分歧明显 (看多 {t.bullish_count} vs 看空 {t.bearish_count})"
            insights.append(insight)
        
        return insights
    
    def _generate_watch_items(self, topics: List[TopicConsensus]) -> List[str]:
        """生成关注事项"""
        watch_items = []
        
        for t in topics:
            if t.consensus_stance == "bullish" and t.consensus_strength > 0.7:
                watch_items.append(f"关注 {t.topic} 的持续性，防止过度拥挤")
            elif t.consensus_stance == "bearish" and t.consensus_strength > 0.7:
                watch_items.append(f"关注 {t.topic} 是否出现反转信号")
            
            if t.risks:
                watch_items.append(f"{t.topic} 风险: {t.risks[0][:50]}")
        
        return list(dict.fromkeys(watch_items))[:5]


# ============================================================
# LLM 增强分析（可选）
# ============================================================

class LLMNarrativeAnalyzer:
    """
    LLM 增强的观点分析器
    
    使用 LLM 进行更深入的观点提炼
    """
    
    def __init__(self, llm_client=None):
        self.llm = llm_client
        self.base_analyzer = NarrativeAnalyzer()
    
    def analyze_with_llm(
        self,
        news_items: List[NewsItem],
        custom_groups: Dict[str, List[str]] = None
    ) -> NarrativeResult:
        """
        使用 LLM 增强分析
        
        先进行规则分析，然后可选地用 LLM 深化
        """
        # 先做规则分析
        result = self.base_analyzer.analyze(news_items, custom_groups)
        
        # 如果有 LLM，进行深化分析
        if self.llm:
            try:
                llm_insights = self._llm_deep_analysis(news_items, result)
                result.key_insights.extend(llm_insights)
            except Exception as e:
                print(f"⚠️ LLM 深化分析失败: {e}")
        
        return result
    
    def _llm_deep_analysis(
        self,
        news_items: List[NewsItem],
        base_result: NarrativeResult
    ) -> List[str]:
        """LLM 深度分析"""
        # 构建新闻摘要
        news_summary = "\n".join([
            f"[{item.source}] {item.title}"
            for item in news_items[:20]  # 最多20条
        ])
        
        prompt = f"""请分析以下财经新闻，提炼市场观点共识：

新闻列表：
{news_summary}

已识别的关键洞察：
{chr(10).join(base_result.key_insights)}

请补充：
1. 可能被忽视的重要观点
2. 潜在的市场风险
3. 值得关注的边际变化

请用简洁的要点形式回答，每个要点一行。"""

        try:
            response = self.llm.generate(prompt)
            insights = [
                line.strip().lstrip('123456789.、- ')
                for line in response.split('\n')
                if line.strip() and len(line.strip()) > 10
            ]
            return insights[:3]
        except Exception as e:
            print(f"❌ LLM 分析失败: {e}")
            return []


# ============================================================
# 测试
# ============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("🧪 观点提炼分析模块测试")
    print("=" * 60)
    
    # 模拟新闻数据
    mock_news = [
        NewsItem(
            source="sina",
            source_id="1",
            title="AI算力需求爆发，服务器厂商订单饱满",
            content="受大模型训练需求推动，AI服务器订单大幅增长，厂商满产",
            url="",
            publish_time="2026-09-23 10:00:00"
        ),
        NewsItem(
            source="sina",
            source_id="2",
            title="半导体设备国产化加速，光刻机突破在即",
            content="国产光刻机取得重大进展，半导体设备替代空间广阔",
            url="",
            publish_time="2026-09-23 11:00:00"
        ),
        NewsItem(
            source="eastmoney",
            source_id="3",
            title="白酒行业库存压力仍存，渠道改革进行时",
            content="白酒企业面临库存去化压力，行业处于调整期",
            url="",
            publish_time="2026-09-23 12:00:00"
        ),
        NewsItem(
            source="sina",
            source_id="4",
            title="新能源车企价格战加剧，行业利润率承压",
            content="多家车企降价促销，行业盈利水平下滑",
            url="",
            publish_time="2026-09-23 13:00:00"
        ),
        NewsItem(
            source="sina",
            source_id="5",
            title="红利资产受追捧，高股息策略占优",
            content="市场震荡下，高股息资产成为资金避风港",
            url="",
            publish_time="2026-09-23 14:00:00"
        ),
    ]
    
    # 测试规则分析器
    print("\n【测试1】规则分析器")
    analyzer = NarrativeAnalyzer()
    result = analyzer.analyze(mock_news)
    
    print(f"\n📊 分析结果:")
    print(f"   新闻总数: {result.total_news}")
    print(f"   整体情绪: {result.overall_sentiment} (置信度 {result.overall_confidence:.0%})")
    
    print(f"\n📈 话题共识:")
    for topic in result.topics[:5]:
        stance_emoji = {"bullish": "🟢", "bearish": "🔴", "neutral": "🟡"}.get(
            topic.consensus_stance, "⚪"
        )
        print(f"   {stance_emoji} {topic.topic}: {topic.consensus_stance} "
              f"(强度 {topic.consensus_strength:.0%})")
        print(f"      提及: {topic.total_mentions} | "
              f"看多: {topic.bullish_count} | 看空: {topic.bearish_count}")
        if topic.key_points:
            print(f"      关键观点: {topic.key_points[0][:40]}...")
    
    print(f"\n💡 关键洞察:")
    for insight in result.key_insights:
        print(f"   {insight}")
    
    print(f"\n⚠️ 风险:")
    for risk in result.risks[:3]:
        print(f"   {risk[:50]}...")
    
    print(f"\n👀 关注事项:")
    for item in result.watch_items[:3]:
        print(f"   {item}")
    
    print("\n" + "=" * 60)
    print("✅ 测试完成")
