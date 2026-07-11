"""
Agentic 投资决策报告生成器

借鉴 Fincept Terminal 的 Deep Agent 框架：
- Planner Agent: 根据可用数据决定需要哪些 specialist
- Specialist Agents: 技术面 / 基本面 / 新闻舆情 / 风险 / 投资经理
- 结构化输出: JSON + Markdown, 便于历史追踪和微信摘要

复用 etf_tracker/llm_report_generator.py 的 LLMProvider
"""
import sys
import os
import json
import re
from datetime import datetime
from typing import Dict, List, Optional, Any

sys.path.insert(0, '/home/zhihu/daily_tracker_analytics/etf_tracker')
sys.path.insert(0, '/home/zhihu/daily_tracker_analytics/etf_tracker/multi_agent')
sys.path.insert(0, '/home/liudawei/github/daily_tracker_analytics/etf_tracker')
sys.path.insert(0, '/home/liudawei/github/daily_tracker_analytics/etf_tracker/multi_agent')

from llm_report_generator import LLMProvider


class MockLLMProvider:
    '''用于本地测试的 Mock LLM Provider，避免调用外部 API。'''

    def __init__(self, responses=None):
        self.responses = responses or {}
        self.call_count = 0

    def call(self, system_prompt: str, user_prompt: str, temperature: float = 0.3, max_tokens: int = 4000) -> str:
        self.call_count += 1
        role = "portfolio-manager"
        lowered = system_prompt.lower()
        if "技术面" in system_prompt or "technical" in lowered:
            role = "technical-analyst"
        elif "基本面" in system_prompt or "估值" in system_prompt or "fundamental" in lowered:
            role = "fundamental-analyst"
        elif "新闻" in system_prompt or "news" in lowered:
            role = "news-analyst"
        elif "风控" in system_prompt or ("风险" in system_prompt and "portfolio" not in lowered):
            role = "risk-manager"

        if role in self.responses:
            return self.responses[role]

        defaults = {
            "technical-analyst": '''```json
{"signal": "bullish", "confidence": 0.72, "trend": "震荡偏多", "key_support": "1.95", "key_resistance": "2.15", "reasoning": "均线多头排列，MACD红柱放大，但接近布林上轨", "catalysts": ["稀土出口政策", "新能源车需求"], "risks": ["冲高回落", "量能不足"]}
```''',
            "fundamental-analyst": '''```json
{"signal": "neutral", "confidence": 0.55, "valuation": "合理", "financial_health": "一般", "reasoning": "ETF无直接基本面，跟踪指数估值处于历史中位", "key_metrics": ["PE适中", "PB适中"], "risks": ["成分股盈利波动"]}
```''',
            "news-analyst": '''```json
{"signal": "neutral", "confidence": 0.60, "sentiment": "中性", "key_themes": ["稀土政策", "新能源"], "reasoning": "新闻情绪中性，政策与需求对冲", "risks": ["政策不确定性"]}
```''',
            "risk-manager": '''```json
{"risk_level": "中", "max_suggested_position": "15%", "stop_loss_guidance": "跌破1.95减仓", "key_risks": ["短期涨幅较大", "板块轮动", "量能不济"], "reasoning": "中高风险，建议控制仓位"}
```''',
            "portfolio-manager": '''```json
{"final_signal": "hold", "final_rating": "中性偏多", "confidence": 0.65, "weighted_score": 62, "target_price_range": ["2.10", "2.20"], "stop_loss": "1.95", "suggested_position": "10%-15%", "action_checklist": ["确认MA20支撑有效", "观察成交量是否持续放大", "稀土政策无利空", "RSI不进入超买", "大盘环境稳定"], "key_reasons": ["技术面向好", "政策支持", "需求有支撑"], "key_risks": ["冲高回落", "量能不足", "政策变化"], "verdict": "稀土ETF技术面中性偏多，短期关注2.15压力，建议持仓观望，等待回调至1.95-2.00区间再考虑加仓。"}
```''',
        }
        return defaults.get(role, "{}")


class AgenticReportGenerator:
    """Agentic 投资决策报告生成器"""

    def __init__(self, llm_provider: Optional[LLMProvider] = None, config: Optional[Dict] = None):
        self.config = config or {}
        llm_cfg = self.config.get("llm_report", {})
        if not llm_provider:
            # 优先用配置 / LLM_REPORT_API_KEY，否则回退到 AUXILIARY_VISION_API_KEY
            if not llm_cfg.get("api_key") and not os.getenv("LLM_REPORT_API_KEY"):
                # 依次尝试 Kimi / Anthropic / OpenAI / Auxiliary Vision
                for env_key, provider, base_url in [
                    ("KIMI_API_KEY", "moonshot", "https://api.moonshot.cn"),
                    ("ANTHROPIC_API_KEY", "anthropic", "https://api.anthropic.com"),
                    ("OPENAI_API_KEY", "openai", "https://api.openai.com"),
                    ("AUXILIARY_VISION_API_KEY", "kimi-coding", "https://api.kimi.com/coding/v1"),
                ]:
                    if os.getenv(env_key):
                        llm_cfg = {
                            **llm_cfg,
                            "provider": llm_cfg.get("provider", provider),
                            "env_api_key": env_key,
                            "env_base_url": "LLM_REPORT_BASE_URL",
                        }
                        if not os.getenv("LLM_REPORT_BASE_URL"):
                            os.environ["LLM_REPORT_BASE_URL"] = base_url
                        break
            llm_provider = LLMProvider(config=llm_cfg)
        self.llm = llm_provider

    # ---------------- 工具函数 ----------------

    @staticmethod
    def _extract_json(text: str) -> Optional[Dict]:
        """从 LLM 输出中提取 JSON"""
        # 先找 ```json ... ```
        match = re.search(r'```json\s*(\{.*?\})\s*```', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except:
                pass
        # 再找第一个 { ... }
        start = text.find('{')
        end = text.rfind('}')
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end+1])
            except:
                pass
        return None

    @staticmethod
    def _format_backtest(backtest_results: List[Dict]) -> str:
        lines = []
        for r in backtest_results:
            period = r.get('period_name') or f"{r.get('days', r.get('period_days', '?'))}天"
            lines.append(
                f"- {period}: 收益 {r.get('total_return', 0):+.1f}%, "
                f"最大回撤 {r.get('max_drawdown', 0):.1f}%, 夏普 {r.get('sharpe', r.get('sharpe_ratio', 0)):.2f}, "
                f"波动率 {r.get('volatility', 0):.1f}%"
            )
        return "\n".join(lines)

    @staticmethod
    def _format_signals(signals: List) -> str:
        lines = []
        for sig in signals[:8]:
            if isinstance(sig, (list, tuple)) and len(sig) >= 3:
                lines.append(f"- {sig[0]} {sig[1]}: {sig[2]}")
            elif isinstance(sig, str):
                lines.append(f"- {sig}")
            elif isinstance(sig, dict):
                lines.append(f"- {sig.get('emoji', '')} {sig.get('name', '')}: {sig.get('desc', '')}")
        return "\n".join(lines)

    @staticmethod
    def _format_tech_snapshot(s: Dict) -> str:
        return (
            f"- 最新价: {s.get('current_price', 'N/A')}\n"
            f"- MA5/10/20/60/120: {s.get('ma5', 'N/A')} / {s.get('ma10', 'N/A')} / {s.get('ma20', 'N/A')} / "
            f"{s.get('ma60', 'N/A')} / {s.get('ma120', 'N/A')}\n"
            f"- MACD DIF/DEA/Hist: {s.get('macd_dif', 0):.4f} / {s.get('macd_dea', 0):.4f} / {s.get('macd_hist', 0):.4f}\n"
            f"- RSI(14): {s.get('rsi_14', 0):.1f}\n"
            f"- 布林上/中/下轨: {s.get('boll_up', 'N/A')} / {s.get('boll_mid', 'N/A')} / {s.get('boll_down', 'N/A')}\n"
            f"- 量比: {s.get('vol_ratio', 0):.2f}x, 年化波动率: {s.get('annual_vol', 0):.1f}%\n"
        )

    @staticmethod
    def _format_fundamentals(f: Dict) -> str:
        if not f:
            return "无基本面数据"
        lines = [
            f"- PE(TTM): {f.get('pe_ratio', 'N/A')}, Forward PE: {f.get('forward_pe', 'N/A')}, PB: {f.get('pb_ratio', 'N/A')}",
            f"- 营收增长: {f.get('revenue_growth', 0):+.1f}%, 毛利率: {f.get('gross_margins', 0):.1f}%, 净利率: {f.get('profit_margins', 0):.1f}%",
            f"- ROE: {f.get('roe', 0):.1f}%, ROA: {f.get('roa', 0):.1f}%, 负债权益比: {f.get('debt_to_equity', 0):.1f}%",
            f"- 股息率: {f.get('dividend_yield', 0):.2f}%, Beta: {f.get('beta', 0):.2f}",
        ]
        return "\n".join(lines)

    @staticmethod
    def _format_news(news_report: Optional[Dict]) -> str:
        if not news_report:
            return "无新闻数据"
        lines = [
            f"- 新闻数量: {news_report.get('news_count', 0)}",
            f"- 情绪得分: {news_report.get('sentiment_score', 0):+.2f}",
            f"- 关键词: {', '.join(news_report.get('keywords', [])[:8])}",
        ]
        for item in news_report.get('news_items', [])[:5]:
            title = item.get('title', '')
            if title and '错误' not in title and title != '暂无最新新闻':
                lines.append(f"  - {title}")
        return "\n".join(lines)

    # ---------------- Planner ----------------

    def _create_plan(self, ticker: str, name: str, data_package: Dict) -> List[Dict[str, str]]:
        """规划需要哪些 specialist"""
        has_fundamental = bool(data_package.get('fundamental_report'))
        has_news = bool(data_package.get('news_report'))

        system = """你是一位任务规划 Agent。请根据可用数据，决定分析一只标的需要哪些 specialist。
必须返回 JSON 数组，每个元素包含 step, specialist, prompt 字段。
可选 specialist: technical-analyst, fundamental-analyst, news-analyst, risk-manager, portfolio-manager。
"""
        user = f"""标的: {name} ({ticker})
可用数据:
- 技术面数据: 有 (回测、技术指标快照、信号)
- 基本面数据: {'有' if has_fundamental else '无'}
- 新闻舆情数据: {'有' if has_news else '无'}

请输出一个 3-5 步的分析计划。示例:
[
  {{"step": "技术面趋势分析", "specialist": "technical-analyst", "prompt": "基于技术指标和回测结果，判断趋势方向、支撑压力、量价关系"}},
  ...
]
"""
        raw = self.llm.call(system, user, temperature=0.1, max_tokens=1500)
        plan = self._extract_json(raw)
        if not plan or not isinstance(plan, list):
            # fallback
            plan = [
                {"step": "技术面分析", "specialist": "technical-analyst", "prompt": "判断趋势、支撑压力、量价关系"},
                {"step": "新闻舆情分析", "specialist": "news-analyst", "prompt": "判断新闻情绪对标的的影响"},
                {"step": "风险评估", "specialist": "risk-manager", "prompt": "评估技术面和情绪面风险"},
                {"step": "投资经理裁决", "specialist": "portfolio-manager", "prompt": "综合各方观点给出最终投资建议"},
            ]
            if has_fundamental:
                plan.insert(1, {"step": "基本面估值分析", "specialist": "fundamental-analyst", "prompt": "判断估值水平和财务健康度"})
        return plan

    # ---------------- Specialist Prompts ----------------

    def _build_specialist_prompt(self, role: str, ticker: str, name: str, data_package: Dict) -> str:
        tech = data_package.get('technical_report', {})
        fund = data_package.get('fundamental_report', {})
        news = data_package.get('news_report', {})

        base = f"""标的: {name} ({ticker})

## 技术面数据
{self._format_tech_snapshot(tech.get('tech_snapshot', {}))}

### 检测到的信号
{self._format_signals(tech.get('signals', []))}

### 多周期回测
{self._format_backtest(tech.get('backtest_results', []))}

### 技术评分
{tech.get('score', 'N/A')}/100, 评级: {tech.get('rating', 'N/A')}
"""
        if role == "technical-analyst":
            return base + """

你是技术面分析师。请基于以上数据给出独立判断。
输出 JSON:
{
  "signal": "bullish | bearish | neutral",
  "confidence": 0.0-1.0,
  "trend": "上升趋势 | 下降趋势 | 震荡整理",
  "key_support": "主要支撑位（具体价格）",
  "key_resistance": "主要压力位（具体价格）",
  "reasoning": "50字以内核心逻辑",
  "catalysts": ["催化1", "催化2"],
  "risks": ["风险1", "风险2"]
}
注意：confidence > 0.8 时必须至少有两个独立技术指标共振。
"""

        if role == "fundamental-analyst":
            if not fund:
                return '无基本面数据，直接返回: {"signal": "neutral", "confidence": 0.0, "reasoning": "无基本面数据"}'
            return base + f"""

## 基本面数据
{self._format_fundamentals(fund.get('fundamentals', {}))}

### 基本面评分
{fund.get('score', 'N/A')}/100, 评级: {fund.get('rating', 'N/A')}

你是基本面/估值分析师。请基于估值、盈利能力、财务杠杆给出独立判断。
输出 JSON:
{{
  "signal": "bullish | bearish | neutral",
  "confidence": 0.0-1.0,
  "valuation": "低估 | 合理 | 高估",
  "financial_health": "健康 | 一般 | 差",
  "reasoning": "50字以内核心逻辑",
  "key_metrics": ["关键指标1", "关键指标2"],
  "risks": ["风险1", "风险2"]
}}
"""

        if role == "news-analyst":
            return base + f"""

## 新闻舆情数据
{self._format_news(news)}

你是新闻舆情分析师。请判断新闻情绪对标的的短期影响。
输出 JSON:
{{
  "signal": "bullish | bearish | neutral",
  "confidence": 0.0-1.0,
  "sentiment": "积极 | 中性 | 消极",
  "key_themes": ["主题1", "主题2"],
  "reasoning": "50字以内核心逻辑",
  "risks": ["风险1", "风险2"]
}}
"""

        if role == "risk-manager":
            return base + f"""

## 其他分析师结论（供参考）
{json.dumps(data_package.get('specialist_outputs', {}), ensure_ascii=False, indent=2)[:1500]}

你是风控官。用户风险偏好：低风险优先。请评估该标的当前风险。
输出 JSON:
{{
  "risk_level": "低 | 中 | 高",
  "max_suggested_position": "建议最大仓位比例，如 10% 或 20%",
  "stop_loss_guidance": "止损思路",
  "key_risks": ["风险1", "风险2", "风险3"],
  "reasoning": "50字以内核心逻辑"
}}
"""

        if role == "portfolio-manager":
            return base + f"""

## 各位 specialist 的结论
{json.dumps(data_package.get('specialist_outputs', {}), ensure_ascii=False, indent=2)[:2000]}

你是投资经理。请综合以上所有 specialist 观点，给出最终投资建议。
注意：用户低风险优先，总资金约5万，已投约7千，后续分批加仓。
输出 JSON:
{{
  "final_signal": "buy | hold | sell | watch",
  "final_rating": "强烈看多 | 偏多 | 中性 | 偏空 | 强烈看空",
  "confidence": 0.0-1.0,
  "weighted_score": 0-100,
  "target_price_range": ["下限", "上限"],
  "stop_loss": "止损价或止损思路",
  "suggested_position": "建议仓位比例，如 5%-15%",
  "action_checklist": ["条件1", "条件2", "条件3", "条件4", "条件5"],
  "key_reasons": ["理由1", "理由2", "理由3"],
  "key_risks": ["风险1", "风险2", "风险3"],
  "verdict": "一段100字以内的综合裁决文字"
}}
约束：
- 当 specialist 观点冲突时，confidence 必须下调，不得超过最高 specialist confidence
- 强烈看多/强烈看空 需要至少 3 个 specialist 中 2 个同向且 risk_level 不为高
- 给出具体 target_price_range 和 stop_loss
"""

        return base

    # ---------------- Execution ----------------

    def _run_specialist(self, role: str, prompt: str) -> Dict[str, Any]:
        """调用 LLM 执行 specialist"""
        system = f"""你是一位专业的金融 {role.replace('-', ' ')}。请严格按用户要求的 JSON 格式输出。
只输出 JSON，不要 Markdown 代码块外的任何解释文字。
所有数值必须精确，禁止编造数据。"""
        raw = self.llm.call(system, prompt, temperature=0.2, max_tokens=2000)
        parsed = self._extract_json(raw)
        if parsed:
            parsed['_raw'] = raw
            return parsed
        return {"error": "JSON 解析失败", "raw": raw}

    def generate(self, ticker: str, name: str,
                 technical_report: Optional[Dict] = None,
                 fundamental_report: Optional[Dict] = None,
                 news_report: Optional[Dict] = None) -> Dict[str, Any]:
        """
        生成 Agentic 投资决策报告
        """
        data_package = {
            'technical_report': technical_report or {},
            'fundamental_report': fundamental_report or {},
            'news_report': news_report or {},
            'specialist_outputs': {},
        }

        # 1. Planning
        plan = self._create_plan(ticker, name, data_package)
        data_package['_plan'] = plan

        # 2. Execute specialists
        for step in plan:
            role = step.get('specialist')
            prompt = self._build_specialist_prompt(role, ticker, name, data_package)
            output = self._run_specialist(role, prompt)
            data_package['specialist_outputs'][role] = output

        # 3. Build final report
        return self._build_final_report(ticker, name, data_package)

    def _build_final_report(self, ticker: str, name: str, data_package: Dict) -> Dict[str, Any]:
        """组装最终报告"""
        outputs = data_package['specialist_outputs']
        pm = outputs.get('portfolio-manager', {})

        # 提取关键字段
        final_signal = pm.get('final_signal', 'watch')
        rating = pm.get('final_rating', '中性')
        confidence = pm.get('confidence', 0.5)
        score = pm.get('weighted_score', 50)
        target = pm.get('target_price_range', [])
        stop_loss = pm.get('stop_loss', '')
        position = pm.get('suggested_position', '')
        checklist = pm.get('action_checklist', [])
        reasons = pm.get('key_reasons', [])
        risks = pm.get('key_risks', [])
        verdict = pm.get('verdict', '')

        # 生成 Markdown 报告
        lines = []
        lines.append(f"# 🏛️ Agentic 投资分析报告")
        lines.append("")
        lines.append(f"**标的**: {name} ({ticker})")
        lines.append(f"**分析日期**: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append(f"## 🏆 最终裁决")
        lines.append("")
        lines.append(f"**评级**: {rating}")
        lines.append(f"**信号**: {final_signal}")
        lines.append(f"**置信度**: {confidence:.0%}")
        lines.append(f"**综合评分**: {score}/100")
        lines.append(f"**目标区间**: {' - '.join(str(x) for x in target) if target else '未给出'}")
        lines.append(f"**止损**: {stop_loss}")
        lines.append(f"**建议仓位**: {position}")
        lines.append("")
        lines.append(f"{verdict}")
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append("## 📊 Specialist 观点")
        lines.append("")
        for role, out in outputs.items():
            if role.startswith('_') or role == 'portfolio-manager':
                continue
            sig = out.get('signal', 'N/A')
            conf = out.get('confidence', 0)
            reason = out.get('reasoning', '')
            lines.append(f"### {role.replace('-', ' ').title()}")
            lines.append(f"- 信号: {sig} | 置信度: {conf:.0%}")
            lines.append(f"- 核心逻辑: {reason}")
            if 'risks' in out:
                lines.append(f"- 风险: {', '.join(out['risks'][:2])}")
            lines.append("")

        lines.append("---")
        lines.append("")
        lines.append("## ✅ 操作检查清单")
        lines.append("")
        for i, item in enumerate(checklist[:6], 1):
            lines.append(f"{i}. {item}")
        lines.append("")

        lines.append("---")
        lines.append("")
        lines.append("## ⚠️ 关键风险")
        lines.append("")
        for i, risk in enumerate(risks[:5], 1):
            lines.append(f"{i}. {risk}")
        lines.append("")

        lines.append("---")
        lines.append("**免责声明**: 本报告由多 Agent 系统自动生成，基于量化模型和历史数据，仅供参考，不构成投资建议。")
        lines.append("")

        report_text = "\n".join(lines)

        return {
            'ticker': ticker,
            'name': name,
            'analysis_date': datetime.now().strftime('%Y-%m-%d %H:%M'),
            'final_signal': final_signal,
            'rating': rating,
            'confidence': confidence,
            'weighted_score': score,
            'target_price_range': target,
            'stop_loss': stop_loss,
            'suggested_position': position,
            'action_checklist': checklist,
            'key_reasons': reasons,
            'key_risks': risks,
            'verdict': verdict,
            'specialist_outputs': outputs,
            'plan': data_package['_plan'],
            'report_text': report_text,
            'structured': pm,
        }


if __name__ == "__main__":
    # 简单测试
    test_tech = {
        'score': 65,
        'rating': '中性偏多',
        'tech_snapshot': {
            'current_price': 2.05,
            'ma5': 2.03, 'ma10': 2.01, 'ma20': 1.98, 'ma60': 1.90, 'ma120': 1.85,
            'macd_dif': 0.012, 'macd_dea': 0.008, 'macd_hist': 0.004,
            'rsi_14': 58.0,
            'boll_up': 2.15, 'boll_mid': 2.00, 'boll_down': 1.85,
            'vol_ratio': 1.2,
            'annual_vol': 35.0,
        },
        'signals': [('🟢', '均线多头排列', 'MA5>MA10>MA20'), ('🟢', 'MACD金叉', 'DIF上穿DEA')],
        'backtest_results': [
            {'period_name': '近30天', 'days': 30, 'total_return': 5.2, 'max_drawdown': -3.1, 'sharpe': 1.2, 'volatility': 30},
            {'period_name': '近60天', 'days': 60, 'total_return': -2.8, 'max_drawdown': -8.5, 'sharpe': -0.3, 'volatility': 35},
            {'period_name': '近90天', 'days': 90, 'total_return': 12.5, 'max_drawdown': -10.2, 'sharpe': 0.8, 'volatility': 38},
        ],
    }
    test_fund = {
        'score': 55,
        'rating': '一般',
        'fundamentals': {
            'pe_ratio': 22.5, 'pb_ratio': 2.1, 'revenue_growth': 8.5,
            'profit_margins': 12.0, 'roe': 10.5, 'debt_to_equity': 45.0,
            'dividend_yield': 1.5, 'beta': 1.1,
        },
    }
    test_news = {
        'news_count': 8,
        'sentiment_score': 0.25,
        'keywords': ['稀土', '出口管制', '新能源车'],
        'news_items': [
            {'title': '稀土出口管制政策延续，海外报价上涨'},
            {'title': '新能源车磁材需求维持高位'},
        ],
    }

    gen = AgenticReportGenerator()
    result = gen.generate('516150', '稀土ETF嘉实', test_tech, test_fund, test_news)
    print(result['report_text'])
    print("\n\n结构化输出:")
    print(json.dumps(result['structured'], ensure_ascii=False, indent=2))
