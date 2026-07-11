#!/usr/bin/env python3
"""
LLM 决策报告生成器
功能：
1. 整合多模型预测、技术指标、回测结果、国际市场、行业新闻、研报观点
2. 生成结构化的投资决策报告（核心结论、评分、买卖点位、操作清单）
3. 支持多种 LLM provider（DeepSeek/Kimi/Anthropic/OpenAI）
4. 与现有 etf_tracker.py 无缝集成

用法：
    generator = LLMReportGenerator(config)
    report = generator.generate_decision_report(etf_result, context)
"""

import os
import json
import time
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import urllib.request
import urllib.parse


class LLMProvider:
    """统一的 LLM 调用器，支持多种 provider"""
    
    def __init__(self, provider: str = "", model: str = "", api_key: str = "", base_url: str = "", config: Optional[Dict] = None):
        cfg = config or {}
        self.provider = (provider or cfg.get("provider") or os.getenv("LLM_REPORT_PROVIDER", "deepseek")).lower().strip()
        self.model = model or cfg.get("model") or os.getenv("LLM_REPORT_MODEL", "")
        # 兼容 kimi-coding / moonshot 等 Moonshot 系 provider 名称
        if self.provider == "kimi":
            self.provider = "moonshot"
        # 支持从配置文件指定的环境变量名读取 key/base_url
        api_key_env = cfg.get("env_api_key", "LLM_REPORT_API_KEY") if isinstance(cfg, dict) else "LLM_REPORT_API_KEY"
        base_url_env = cfg.get("env_base_url", "LLM_REPORT_BASE_URL") if isinstance(cfg, dict) else "LLM_REPORT_BASE_URL"
        self.api_key = api_key or os.getenv(api_key_env, "")
        self.base_url = base_url or os.getenv(base_url_env, "")
        
        # 默认模型映射
        self._default_models = {
            "deepseek": "deepseek-v4-flash",
            "kimi": "kimi-k2-5-or-latest",
            "kimi-coding": "k2p5",
            "kimi-for-coding": "k2p5",
            "moonshot": "kimi-k2-5-or-latest",
            "openai": "gpt-4o-mini",
            "anthropic": "claude-3-5-sonnet-20241022",
            "openrouter": "deepseek/deepseek-v4"
        }
        
        if not self.model:
            self.model = self._default_models.get(self.provider, "deepseek-v4-flash")
        
        # 默认 base URL 映射
        if not self.base_url:
            self.base_url = self._infer_base_url()
    
    def _infer_base_url(self) -> str:
        """根据 provider 推断 base URL"""
        if self.provider == "deepseek":
            return "https://api.deepseek.com"
        elif self.provider in ("kimi-coding", "kimi-for-coding"):
            return "https://api.kimi.com/coding/v1"
        elif self.provider in ("kimi", "moonshot"):
            return "https://api.moonshot.cn"
        elif self.provider == "openai":
            return "https://api.openai.com"
        elif self.provider == "anthropic":
            return "https://api.anthropic.com"
        elif self.provider == "openrouter":
            return "https://openrouter.ai/api"
        return ""
    
    def _build_messages(self, system_prompt: str, user_prompt: str) -> List[Dict]:
        """构建消息"""
        if self.provider == "anthropic":
            return [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ]
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
    
    def _call_openai_compatible(self, messages: List[Dict], temperature: float = 0.3, max_tokens: int = 4000) -> str:
        """调用 OpenAI-compatible API"""
        if self.provider == "kimi-coding":
            # Kimi Coding API: https://api.kimi.com/coding/v1/chat/completions
            url = f"{self.base_url.rstrip('/')}/chat/completions"
        else:
            url = f"{self.base_url.rstrip('/')}/v1/chat/completions"
        
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens
        }
        # kimi-coding 部分模型仅支持 temperature=1
        if self.provider == "kimi-coding":
            payload["temperature"] = 1
        else:
            payload["temperature"] = temperature
        
        req = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}"
            },
            method="POST"
        )
        
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            return data['choices'][0]['message']['content']
        return ""
    
    def _call_anthropic(self, messages: List[Dict], temperature: float = 0.3, max_tokens: int = 4000) -> str:
        """调用 Anthropic API"""
        # 找到 system 和 user
        system = ""
        user_content = ""
        for m in messages:
            if m['role'] == 'system':
                system = m['content']
            elif m['role'] == 'user':
                user_content = m['content']
        
        url = "https://api.anthropic.com/v1/messages"
        payload = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": system,
            "messages": [{"role": "user", "content": user_content}]
        }
        
        req = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
            headers={
                "Content-Type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01"
            },
            method="POST"
        )
        
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            return data['content'][0]['text']
    
    def call(self, system_prompt: str, user_prompt: str, temperature: float = 0.3, max_tokens: int = 4000) -> str:
        """统一的 LLM 调用入口"""
        if not self.api_key:
            raise ValueError(f"LLM provider {self.provider} 未配置 API key")
        
        messages = self._build_messages(system_prompt, user_prompt)
        
        max_retries = 2
        for attempt in range(max_retries):
            try:
                if self.provider == "anthropic":
                    return self._call_anthropic(messages, temperature, max_tokens)
                else:
                    return self._call_openai_compatible(messages, temperature, max_tokens)
            except Exception as e:
                if attempt < max_retries - 1:
                    print(f"  LLM 调用失败（第 {attempt+1} 次）: {e}，重试中...")
                    time.sleep(2)
                else:
                    raise


class LLMReportGenerator:
    """LLM 投资决策报告生成器"""
    
    def __init__(self, config=None, llm_provider: Optional[LLMProvider] = None):
        self.config = config or {}
        if isinstance(self.config, dict):
            self.config_dict = self.config
        else:
            self.config_dict = getattr(self.config, 'config', {}) or {}
        
        # 从配置读取 llm_report 子配置并传给 LLMProvider
        llm_cfg = self.config_dict.get("llm_report", {}) if isinstance(self.config_dict, dict) else {}
        self.llm = llm_provider or LLMProvider(config=llm_cfg)
    
    # ---------------- 数据格式化 ----------------
    
    def _format_backtest(self, backtest_results: List[Dict]) -> str:
        """格式化回测结果"""
        lines = []
        for r in backtest_results:
            lines.append(
                f"- {r['period_days']}天 ({r.get('period_desc', '')}): "
                f"收益 {r['total_return']:+.2f}%, 最大回撤 {r['max_drawdown']:.2f}%, "
                f"夏普 {r['sharpe_ratio']:.2f}, 日胜率 {r['win_rate']:.1f}%, "
                f"RSI {r['latest_rsi']:.1f} ({r['rsi_status']}), 均线 {r['ma_trend']}, "
                f"布林带位置 {r['bb_position']:.1%}, 量比 {r['volume_ratio']:.2f}x"
            )
        return "\n".join(lines)
    
    def _format_signals(self, signals: Dict, position: Dict) -> str:
        """格式化交易信号"""
        SIGNAL_MAP = {'bullish': '看多', 'bearish': '看空', 'neutral': '中性'}
        lines = [
            f"- 综合评分: {signals['score']}/100",
            f"- 整体信号: {SIGNAL_MAP.get(signals['overall'], signals['overall'])}",
            f"- 操作建议: {signals['action']}",
            f"- 建议仓位: {position.get('suggested_position', '-')}",
            f"- 止损位: {position.get('stop_loss', '-')}",
            f"- 第一目标位: {position.get('take_profit_1', '-')}",
            f"- 第二目标位: {position.get('take_profit_2', '-')}",
            "- 详细信号:"
        ]
        for sig in signals.get('signals', []):
            lines.append(f"  - {sig}")
        return "\n".join(lines)
    
    def _format_multi_model(self, multi_model: Dict) -> str:
        """格式化多模型预测"""
        if not multi_model:
            return "暂无多模型预测数据"
        
        ensemble = multi_model.get('ensemble', {})
        lines = [
            f"- 集成预测1日收益: {ensemble.get('return_1d', 0)*100:+.2f}%",
            f"- 预测1日价格: {ensemble.get('price_1d', 0):.3f}",
            f"- 置信度: {ensemble.get('confidence', 0)*100:.1f}%"
        ]
        
        individual = multi_model.get('individual', {})
        if individual:
            lines.append("- 各模型预测对比:")
            for model_name, pred in individual.items():
                lines.append(
                    f"  - {model_name}: {pred.get('return_1d', 0)*100:+.2f}% "
                    f"(价格 {pred.get('price_1d', 0):.3f})"
                )
        return "\n".join(lines)
    
    def _format_international(self, international_data: Dict) -> str:
        """格式化国际市场数据"""
        if not international_data:
            return "暂无国际市场数据"
        
        lines = []
        # 美股
        for key in ['^DJI', '^GSPC', '^IXIC']:
            if key in international_data:
                d = international_data[key]
                lines.append(f"- {d['name']}: {d['price']:,.2f} ({d['change_pct']:+.2f}%, 30日 {d['return_30d']:+.2f}%)")
        # VIX
        if '^VIX' in international_data:
            v = international_data['^VIX']
            lines.append(f"- VIX: {v['price']:.2f} ({v.get('level', '未知')})")
        # 商品
        for key in ['CL=F', 'GC=F', 'SI=F', 'HG=F']:
            if key in international_data:
                d = international_data[key]
                lines.append(f"- {d['name']}: {d['price']:.2f} ({d['change_pct']:+.2f}%)")
        # 汇率
        for key in ['CNY=X', 'CNH=F', 'DX-Y.NYB']:
            if key in international_data:
                d = international_data[key]
                lines.append(f"- {d['name']}: {d['price']:.4f} ({d['change_pct']:+.2f}%)")
        # 港股
        for key in ['^HSI', '513180']:
            if key in international_data:
                d = international_data[key]
                lines.append(f"- {d['name']}: {d['price']:.2f} ({d['change_pct']:+.2f}%)")
        return "\n".join(lines)
    
    def _format_news(self, news_data: Dict) -> str:
        """格式化行业新闻"""
        if not news_data:
            return "暂无行业新闻数据"
        
        lines = []
        for industry, data in news_data.items():
            if isinstance(data, list):
                # data 是新闻列表（来自 get_multi_industry_news）
                emoji = "📊"
                lines.append(f"- {emoji} {industry}: 共 {len(data)} 条新闻")
                for i, news in enumerate(data[:3]):
                    s = news.get('sentiment', 'neutral')
                    e = "📈" if s == 'positive' else "📉" if s == 'negative' else "📊"
                    lines.append(f"  {i+1}. {e} {news.get('title', '')}")
                continue
            
            emoji = "📈" if data.get('sentiment') == 'positive' else "📉" if data.get('sentiment') == 'negative' else "📊"
            lines.append(
                f"- {emoji} {industry}: 情绪 {data.get('sentiment', 'neutral')} "
                f"(得分 {data.get('sentiment_score', 0):.2f}, 新闻 {data.get('news_count', 0)} 条, "
                f"正面 {data.get('positive_ratio', 0)}%, 负面 {data.get('negative_ratio', 0)}%)"
            )
            # 只展示前3条新闻标题
            for i, news in enumerate(data.get('news_list', [])[:3]):
                lines.append(f"  {i+1}. {news.get('title', '')}")
        return "\n".join(lines)
    
    def _format_research(self, research_summary: str) -> str:
        """格式化研报摘要"""
        if not research_summary:
            return "暂无研报摘要"
        # 截断到 1000 字，避免 prompt 过长
        return research_summary[:1000] + ("..." if len(research_summary) > 1000 else "")
    
    # ---------------- Prompt 生成 ----------------
    
    def _build_system_prompt(self) -> str:
        """构建系统提示"""
        return """你是一位资深 ETF 量化策略分析师，擅长结合技术指标、多模型预测、国际市场、行业新闻和研报观点，生成结构化的投资决策报告。

你的任务是根据输入数据，生成一份专业的中文投资规划报告。报告必须包含以下部分：

1. 核心结论（看多/看空/观望，并给出置信度）
2. 关键理由（3-5 条，结合数据、新闻、国际市场）
3. 关键指标速览（最新价、30/60/90天收益、综合评分、多模型预测、RSI、MACD、均线、布林带位置、量比）
4. 催化因素（近期可能推动 ETF 上涨或下跌的 3-5 个因素）
5. 风险警报（3-5 条风险）
6. 操作建议（买入/卖出/持有/观望，并给出具体价位区间）
7. 操作检查清单（入场前必须确认的 5 个条件）
8. 板块对比（如果是多 ETF 报告，给出强势/弱势板块）

要求：
- 语言专业、简洁，避免空话套话
- 每个结论必须有数据支撑
- 操作建议必须具体（包含目标价、止损价、仓位比例）
- 风险提示必须明确
- 不要输出任何免责声明之外的不确定表述
- 使用 Markdown 格式
"""
    
    def _build_single_etf_prompt(self, etf_result: Dict, context: Dict) -> str:
        """构建单只 ETF 的 prompt"""
        code = etf_result.get('code', '')
        name = etf_result.get('name', '')
        theme = etf_result.get('theme', '')
        sector = etf_result.get('sector', '')
        current_price = etf_result.get('current_price', 0)
        
        backtest = etf_result.get('backtest', [])
        signals = etf_result.get('signals', {})
        position = etf_result.get('position', {})
        multi_model = etf_result.get('multi_model', {})
        
        international = context.get('international_data', {})
        news = context.get('news_data', {})
        research = context.get('research_summary', '')
        
        prompt = f"""请分析以下 ETF 并生成投资决策报告。

## 基本信息
- ETF 名称: {name}
- ETF 代码: {code}
- 主题/板块: {theme} / {sector}
- 最新价: {current_price:.3f}

## 多周期回测结果
{self._format_backtest(backtest)}

## 交易信号与仓位建议
{self._format_signals(signals, position)}

## 多模型交叉验证预测
{self._format_multi_model(multi_model)}

## 国际市场环境
{self._format_international(international)}

## 行业新闻与舆情
{self._format_news(news)}

## 机构研报观点
{self._format_research(research)}

请严格按照系统提示中的 8 个部分输出报告。每个部分使用二级标题（##）。
"""
        return prompt
    
    def _build_multi_etf_prompt(self, analyzer_results: List[Dict], sector_ranking, context: Dict) -> str:
        """构建多 ETF 对比的 prompt"""
        # 把 sector_ranking DataFrame 转换为 dict 列表
        if sector_ranking is None:
            sector_ranking = []
        elif hasattr(sector_ranking, 'to_dict'):
            sector_ranking = sector_ranking.to_dict('records')
        
        # 生成板块排名摘要
        ranking_lines = []
        for i, row in enumerate(sector_ranking):
            ranking_lines.append(
                f"{i+1}. {row.get('板块', '-')}/{row.get('主题', '-')} — "
                f"60天收益 {row.get('60天收益', 0):+.2f}%, 评分 {row.get('综合评分', 0):.1f}, "
                f"信号 {row.get('信号', '-')}, 1日预测 {row.get('1日预测', 0):+.2f}%"
            )
        ranking_text = "\n".join(ranking_lines)
        
        # 详细分析前5名
        detail_lines = []
        for result in analyzer_results[:5]:
            if 'error' in result:
                continue
            detail_lines.append(
                f"- {result.get('name', '')} ({result.get('code', '')}): "
                f"最新价 {result.get('current_price', 0):.3f}, 评分 {result.get('signals', {}).get('score', 0):.1f}, "
                f"仓位建议 {result.get('position', {}).get('suggested_position', '-')}, "
                f"多模型1日预测 {result.get('multi_model', {}).get('ensemble', {}).get('return_1d', 0)*100:+.2f}%"
            )
        detail_text = "\n".join(detail_lines)
        
        international = context.get('international_data', {})
        news = context.get('news_data', {})
        research = context.get('research_summary', '')
        
        prompt = f"""请分析以下多板块 ETF 组合并生成综合投资决策报告。

## 板块综合排名
{ranking_text}

## 重点板块详情
{detail_text}

## 国际市场环境
{self._format_international(international)}

## 行业新闻与舆情
{self._format_news(news)}

## 机构研报观点
{self._format_research(research)}

请严格按照系统提示中的 8 个部分输出报告。每个部分使用二级标题（##）。
"""
        return prompt
    
    # ---------------- 报告生成 ----------------
    
    def generate_single_etf_report(self, etf_result: Dict, context: Dict = None) -> str:
        """生成单只 ETF 的 LLM 决策报告"""
        context = context or {}
        system_prompt = self._build_system_prompt()
        user_prompt = self._build_single_etf_prompt(etf_result, context)
        
        return self.llm.call(system_prompt, user_prompt, temperature=0.3, max_tokens=4000)
    
    def generate_multi_etf_report(self, analyzer_results: List[Dict], sector_ranking, context: Dict = None) -> str:
        """生成多 ETF 对比的 LLM 决策报告"""
        context = context or {}
        system_prompt = self._build_system_prompt()
        user_prompt = self._build_multi_etf_prompt(analyzer_results, sector_ranking, context)
        
        return self.llm.call(system_prompt, user_prompt, temperature=0.3, max_tokens=4000)
    
    # ---------------- 与 etf_tracker 集成 ----------------
    
    def generate_context(self, international_data: Optional[Dict] = None, news_data: Optional[Dict] = None, research_summary: str = "") -> Dict:
        """生成 LLM 报告上下文"""
        return {
            "international_data": international_data or {},
            "news_data": news_data or {},
            "research_summary": research_summary or ""
        }
    
    def enrich_multi_etf_report(self, base_report: str, analyzer_results: List[Dict], sector_ranking,
                                international_data: Optional[Dict] = None, news_data: Optional[Dict] = None,
                                research_summary: str = "") -> str:
        """在原有报告基础上，追加 LLM 决策报告章节"""
        context = self.generate_context(international_data, news_data, research_summary)
        
        try:
            llm_report = self.generate_multi_etf_report(analyzer_results, sector_ranking, context)
            return base_report + "\n\n---\n\n# LLM 智能决策报告\n\n" + llm_report + "\n"
        except Exception as e:
            print(f"  LLM 报告生成失败: {e}")
            return base_report + "\n\n---\n\n# LLM 智能决策报告\n\n> LLM 报告生成失败: " + str(e) + "\n"


if __name__ == "__main__":
    # 测试用例
    generator = LLMReportGenerator()
    
    test_etf_result = {
        "code": "516150",
        "name": "稀土ETF嘉实",
        "theme": "稀土永磁",
        "sector": "稀土永磁",
        "current_price": 2.046,
        "backtest": [
            {"period_days": 30, "period_desc": "短期", "total_return": 5.2, "max_drawdown": -3.1, "sharpe_ratio": 1.2, "win_rate": 55.0, "latest_rsi": 62.5, "rsi_status": "中性偏多", "ma_trend": "多头排列", "bb_position": 0.65, "volume_ratio": 1.2},
            {"period_days": 60, "period_desc": "中期", "total_return": -2.8, "max_drawdown": -8.5, "sharpe_ratio": -0.3, "win_rate": 48.0, "latest_rsi": 58.0, "rsi_status": "中性", "ma_trend": "震荡", "bb_position": 0.55, "volume_ratio": 1.0},
            {"period_days": 90, "period_desc": "中长期", "total_return": 12.5, "max_drawdown": -10.2, "sharpe_ratio": 0.8, "win_rate": 52.0, "latest_rsi": 60.0, "rsi_status": "中性偏多", "ma_trend": "多头排列", "bb_position": 0.7, "volume_ratio": 1.3},
        ],
        "signals": {
            "score": 65.0,
            "overall": "bullish",
            "action": "建议关注买入机会",
            "signals": ["价格站上 MA5/MA10", "成交量温和放大", "MACD 金叉"]
        },
        "position": {
            "suggested_position": "30.0%",
            "stop_loss": 1.95,
            "take_profit_1": 2.15,
            "take_profit_2": 2.25
        },
        "multi_model": {
            "ensemble": {
                "return_1d": 0.005,
                "price_1d": 2.056,
                "confidence": 0.62
            }
        }
    }
    
    test_context = {
        "international_data": {
            "^GSPC": {"name": "标普500", "price": 7499.36, "change_pct": 0.79, "return_30d": 3.2},
            "GC=F": {"name": "COMEX黄金", "price": 3984.6, "change_pct": -0.95, "return_30d": 2.1}
        },
        "news_data": {
            "稀土永磁": {
                "sentiment": "positive",
                "sentiment_score": 0.35,
                "news_count": 12,
                "positive_ratio": 58.3,
                "negative_ratio": 16.7,
                "news_list": [
                    {"title": "稀土出口管制政策延续，海外报价上涨"},
                    {"title": "新能源车磁材需求维持高位"}
                ]
            }
        },
        "research_summary": "机构研报普遍看好稀土永磁长期需求，短期关注稀土价格波动。"
    }
    
    report = generator.generate_single_etf_report(test_etf_result, test_context)
    print(report)
