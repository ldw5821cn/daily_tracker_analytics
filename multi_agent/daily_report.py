#!/usr/bin/env python3
"""
每日盘前报告生成器 - 整合 P0-P4 + 现有系统

功能：
1. 抓取当日情绪数据（P2）
2. 分析题材雷达（P3）
3. 推荐游资心法（P4）
4. 读取证据链数据（P0）
5. 读取大V观点共识（P1）
6. 生成统一盘前报告（Markdown + 微信摘要）

用法：
    python3 multi_agent/daily_report.py              # 生成今日报告
    python3 multi_agent/daily_report.py --date 20260923  # 指定日期
    python3 multi_agent/daily_report.py --push       # 生成并推送微信
"""

import sys
import os
import json
import argparse
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
REPO = BASE.parent

from sentiment_analyzer import SentimentAnalyzer, PHASE_EBB, PHASE_CLimax, PHASE_ICE, PHASE_REPAIR, PHASE_START
from theme_radar import ThemeRadar
from trader_wisdom import TraderWisdomLibrary
try:
    from kronos_predictor import KronosStockPredictor  # 新增 Kronos
except Exception:
    KronosStockPredictor = None  # Kronos 依赖 torch，缺失时静默降级

# 尝试导入 HiThink 客户端
try:
    from hithink_client import HiThinkFinanceClient
    HITHINK_AVAILABLE = True
except ImportError:
    try:
        from multi_agent.hithink_client import HiThinkFinanceClient
        HITHINK_AVAILABLE = True
    except ImportError:
        HiThinkFinanceClient = None
        HITHINK_AVAILABLE = False


class DailyReportGenerator:
    """每日盘前报告生成器"""

    def __init__(self, output_dir: str = None):
        self.output_dir = output_dir or str(REPO / 'docs')
        self.wisdom_lib = TraderWisdomLibrary()

    def run(self, date: str = None, push: bool = False) -> dict:
        """生成完整盘前报告"""
        print("=" * 60)
        print("📋 每日盘前报告生成")
        print("=" * 60)

        # 1. 情绪分析（P2）
        print("\n[1/5] 情绪周期分析...")
        sentiment = SentimentAnalyzer().analyze(target_date=date)
        print(f"   ✅ {sentiment.date} → {sentiment.phase} ({sentiment.phase_confidence:.0%})")

        # 2. 题材雷达（P3）
        print("\n[2/5] 题材雷达分析...")
        theme = ThemeRadar().analyze()
        print(f"   ✅ 活跃题材: {len(theme.themes)}个")

        # 3. 游资心法推荐（P4）
        print("\n[3/5] 游资心法推荐...")
        wisdom = self.wisdom_lib.recommend_by_phase(sentiment.phase)
        print(f"   ✅ 推荐 {len(wisdom)}篇心法")

        # 4. 读取 P0 证据链（如果有当日数据）
        print("\n[4/5] 读取证据链数据...")
        evidence_data = self._load_latest_evidence()
        if isinstance(evidence_data, dict):
            ev_list = evidence_data.get('evidence_chain', [])
        elif isinstance(evidence_data, list):
            ev_list = evidence_data
        else:
            ev_list = []
        print(f"   {'✅ 找到 ' + str(len(ev_list)) + ' 条证据链' if ev_list else '⚠️ 暂无当日证据链'}")

        # 5. Kronos AI 预测（新增）
        print("\n[5/7] Kronos K线预测...")
        kronos = self._run_kronos_prediction()
        print(f"   ✅ 预测完成: {len(kronos.get('predictions', []))} 只")
        
        # 6. HiThink 估值数据（新增）
        print("\n[6/7] HiThink 估值数据...")
        valuation = self._run_valuation_analysis()
        print(f"   {'✅ 估值分析完成' if valuation else '⚠️ 估值数据不可用'}")
        
        # 7. 读取 P1 大V观点
        print("\n[7/7] 读取大V观点共识...")
        narrative_data = self._load_latest_narrative()
        print(f"   {'✅ 找到观点数据' if narrative_data else '⚠️ 暂无当日观点数据'}")

        # 构建报告
        report = self._build_report(
            sentiment=sentiment,
            theme=theme,
            wisdom=wisdom,
            evidence=evidence_data,
            narrative=narrative_data,
            kronos=kronos,  # 传入 Kronos 预测
            valuation=valuation,  # 传入估值数据
        )

        # 保存
        report_date = sentiment.date.replace('-', '')
        md_path = os.path.join(self.output_dir, f'daily_report_{report_date}.md')
        with open(md_path, 'w', encoding='utf-8') as f:
            f.write(report['markdown'])
        print(f"\n✅ Markdown 报告: {md_path}")

        # 生成微信摘要
        wechat = self._build_wechat_summary(report)
        print("\n📱 微信摘要:")
        print("-" * 60)
        print(wechat)
        print("-" * 60)

        return {
            'report': report,
            'md_path': md_path,
            'wechat_summary': wechat,
        }

    def _run_kronos_prediction(self, symbols: list = None) -> dict:
        """运行 Kronos 预测（默认预测用户持仓）"""
        if symbols is None:
            symbols = ['000001', '000598', '000027', '600011', '600027', '600023', '600642', '601398']
        
        try:
            predictor = KronosStockPredictor(model_size="mini")
            predictions = []
            
            # 预测所有持仓（8只）
            for symbol in symbols:
                try:
                    result = predictor.predict(symbol, pred_days=5, sample_count=30)
                    if 'error' not in result:
                        # 添加中文名称
                        name_map = {
                            '000001': '平安银行',
                            '000598': '兴蓉环境',
                            '000027': '深圳能源',
                            '600011': '华能国际',
                            '600027': '华电国际',
                            '600023': '浙能电力',
                            '600642': '申能股份',
                            '601398': '工商银行',
                        }
                        result['name'] = name_map.get(symbol, symbol)
                        predictions.append(result)
                except Exception as e:
                    print(f"   ⚠️ {symbol}: {e}")
            
            return {'predictions': predictions}
        except Exception as e:
            print(f"   ⚠️ Kronos 加载失败: {e}")
            return {'predictions': [], 'error': str(e)}

    def _run_valuation_analysis(self) -> dict:
        """运行 HiThink 估值分析"""
        if not HITHINK_AVAILABLE or HiThinkFinanceClient is None:
            return {}
        
        try:
            client = HiThinkFinanceClient()
            
            # 用户持仓
            holdings = ['000001', '000598', '000027', '600011', '600027', '600023', '600642', '601398']
            thscodes = [HiThinkFinanceClient.to_thscode(s) for s in holdings]
            
            # 获取估值数据
            valuations = client.get_valuation(thscodes)
            
            # 添加中文名称
            name_map = {
                '000001': '平安银行',
                '000598': '兴蓉环境',
                '000027': '深圳能源',
                '600011': '华能国际',
                '600027': '华电国际',
                '600023': '浙能电力',
                '600642': '申能股份',
                '601398': '工商银行',
            }
            
            for v in valuations:
                ticker = HiThinkFinanceClient.from_thscode(v.get('thscode', ''))
                v['name'] = name_map.get(ticker, ticker)
                v['ticker'] = ticker
            
            return {'valuations': valuations}
        except Exception as e:
            print(f"   ⚠️ HiThink 估值分析失败: {e}")
            return {}

    def _load_latest_evidence(self) -> list:
        """读取最新证据链数据"""
        evidence_dir = REPO / 'multi_agent' / 'data' / 'evidence'
        if not evidence_dir.exists():
            return []
        files = sorted(evidence_dir.glob('*.json'), reverse=True)
        if not files:
            return []
        try:
            with open(files[0]) as f:
                return json.load(f)
        except:
            return []

    def _load_latest_narrative(self) -> dict:
        """读取最新大V观点数据"""
        narrative_dir = REPO / 'docs' / 'narrative_data'
        if not narrative_dir.exists():
            return {}
        files = sorted(narrative_dir.glob('*.json'), reverse=True)
        if not files:
            return {}
        try:
            with open(files[0]) as f:
                return json.load(f)
        except:
            return {}

    def _build_report(self, sentiment, theme, wisdom, evidence, narrative, kronos=None, valuation=None) -> dict:
        """构建完整报告"""
        m = sentiment.metrics

        # Markdown 报告
        md = f"""# 📋 每日盘前报告

**日期**: {sentiment.date}
**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

---

## 一、情绪周期（P2）

**当前阶段**: {sentiment.phase} (置信度 {sentiment.phase_confidence:.0%})

| 指标 | 数值 |
|------|------|
| 涨停 | {m.zt_count} |
| 跌停 | {m.dt_count} |
| 炸板 | {m.zb_count} (封板率{m.seal_rate:.0%}) |
| 最高连板 | {m.max_streak} |
| 昨日涨停溢价 | {m.prev_avg_chg:+.2f}% (上涨占比{m.prev_up_ratio:.0%}) |

"""

        # 交易提示
        if sentiment.signals:
            md += "**交易提示**:\n"
            for s in sentiment.signals:
                md += f"- {s}\n"
            md += "\n"

        # 风险提示
        if sentiment.risks:
            md += "**风险提示**:\n"
            for r in sentiment.risks:
                md += f"- {r}\n"
            md += "\n"

        # Kronos AI 预测（新增）
        if kronos and kronos.get('predictions'):
            md += "---\n\n## 二、Kronos AI 预测\n\n"
            md += "| 标的 | 现价 | 预期收益 | 上涨概率 | 信号 |\n"
            md += "|------|------|----------|----------|------|\n"
            for pred in kronos['predictions']:
                name = pred.get('name', pred['symbol'])
                md += f"| {name} | {pred['current_price']:.2f} | {pred['expected_return']*100:+.2f}% | {pred['bull_prob']*100:.0f}% | {pred['signal']} |\n"
            md += "\n"

        md += "---\n\n## 三、估值分析（HiThink）\n\n"
        if valuation and valuation.get('valuations'):
            md += "| 标的 | PE(TTM) | PB(MRQ) | PS(TTM) | PC(TTM) |\n"
            md += "|------|---------|---------|---------|---------|\n"
            for v in valuation['valuations']:
                pe = v.get('pe_ttm', '-')
                pb = v.get('pb_mrq', '-')
                ps = v.get('ps_ttm', '-')
                pc = v.get('pc_ttm', '-')
                md += f"| {v['name']} | {pe if pe != '-' else 'N/A'} | {pb if pb != '-' else 'N/A'} | {ps if ps != '-' else 'N/A'} | {pc if pc != '-' else 'N/A'} |\n"
            md += "\n"
            md += "*注: PE=市盈率, PB=市净率, PS=市销率, PC=市现率*\n\n"
        else:
            md += "*估值数据暂不可用（需要 HiThink API）*\n\n"

        md += "---\n\n## 四、题材雷达（P3）\n\n"
        md += f"{theme.market_summary}\n\n"

        if theme.themes:
            md += "| 题材 | 涨停 | 最高板 | 封单均值 | 状态 |\n"
            md += "|------|------|--------|----------|------|\n"
            for t in theme.themes[:5]:
                zt_count = t.get('zt_count', 0)
                status = "🔥 活跃" if zt_count >= 3 else "⚡ 观察"
                md += f"| {t['name']} | {zt_count} | {t.get('max_board', '-')} | {t.get('avg_fund', 0):.0f}万 | {status} |\n"
            md += "\n"

        if theme.avoid:
            md += f"**回避名单**: {', '.join(theme.avoid)}\n\n"

        md += "---\n\n## 五、游资心法推荐（P4）\n\n"
        if wisdom:
            md += f"针对 **{sentiment.phase}**，推荐以下心法：\n\n"
            for w in wisdom[:5]:
                doc = w['doc']
                md += f"### {doc['trader_name']} - {doc['title']}\n"
                md += f"- 匹配原因: {w['match_reason']}\n"
                if doc.get('quote'):
                    md += f"- 名言: \"{doc['quote'][:80]}{'...' if len(doc['quote']) > 80 else ''}\"\n"
                md += "\n"

        md += "---\n\n## 六、大V观点共识（P1）\n\n"
        if narrative:
            md += "*(当日观点数据已生成，详见 narrative_report.html)*\n\n"
        else:
            md += "*暂无当日观点数据*\n\n"

        md += "---\n\n## 七、证据链追踪（P0）\n\n"
        if evidence:
            # 确保证据是列表格式
            if isinstance(evidence, list):
                md += f"当日证据链: {len(evidence)} 条\n\n"
                for ev in evidence[:3]:
                    if isinstance(ev, dict):
                        md += f"- {ev.get('ticker', 'N/A')}: {ev.get('headline', 'N/A')}\n"
            else:
                md += f"当日证据链: {type(evidence).__name__}\n\n"
        else:
            md += "*暂无当日证据链数据*\n\n"

        md += "---\n\n## 八、综合建议\n\n"
        md += self._generate_advice(sentiment, theme, kronos, valuation)  # 传入 kronos 和 valuation
        
        md += f"\n\n---\n*报告生成: LLM-native 量化系统 v1.0 | 含 Kronos AI 预测 + HiThink 估值*\n"

        return {
            'date': sentiment.date,
            'phase': sentiment.phase,
            'markdown': md,
            'sentiment': sentiment.to_dict(),
            'theme': theme.to_dict(),
            'wisdom_count': len(wisdom),
        }

    def _generate_advice(self, sentiment, theme, kronos=None, valuation=None) -> str:
        """生成综合建议"""
        advice = []

        # 基于情绪周期
        phase_advice = {
            PHASE_ICE: "🧊 冰点期：情绪出清中，控制仓位，关注率先企稳方向",
            PHASE_REPAIR: "🌱 修复期：情绪回暖，可小仓位试错新题材前排",
            PHASE_START: "🔥 启动期：情绪健康，按系统信号正常执行",
            PHASE_CLimax: "⚠️ 高潮期：打板过热，兑现为主，勿追高接力",
            PHASE_EBB: "🌊 退潮期：亏钱效应扩散，减仓防守，等待冰点信号",
        }
        advice.append(phase_advice.get(sentiment.phase, "按系统信号执行"))

        # 基于题材
        if theme.themes:
            top = theme.themes[0]
            advice.append(f"最强题材: {top.get('name', '-')}（{top.get('zt_count', 0)}只涨停），关注首板补涨机会")

        if theme.avoid:
            advice.append(f"回避: {', '.join(theme.avoid)}（高位风险）")

        # 基于封板率
        m = sentiment.metrics
        if m.seal_rate < 0.7:
            advice.append(f"封板率 {m.seal_rate:.0%} 偏低，盘面分歧大，持仓需分散")

        # 基于 Kronos 预测（新增）
        if kronos and kronos.get('predictions'):
            bullish = [p for p in kronos['predictions'] if p['bull_prob'] > 0.6]
            bearish = [p for p in kronos['predictions'] if p['bull_prob'] < 0.4]
            
            if bullish:
                names = [p.get('name', p['symbol']) for p in bullish]
                advice.append(f"Kronos看涨: {', '.join(names)}（短期强势）")
            
            if bearish:
                names = [p.get('name', p['symbol']) for p in bearish]
                advice.append(f"Kronos看跌: {', '.join(names)}（注意风险）")
        
        # 基于估值（新增）
        if valuation and valuation.get('valuations'):
            low_pb = [v for v in valuation['valuations'] if v.get('pb_mrq') and v['pb_mrq'] < 1.0]
            high_pe = [v for v in valuation['valuations'] if v.get('pe_ttm') and v['pe_ttm'] > 20]
            
            if low_pb:
                names = [v['name'] for v in low_pb]
                advice.append(f"低 PB 标的: {', '.join(names)}（破净，安全边际高）")
            
            if high_pe:
                names = [v['name'] for v in high_pe]
                advice.append(f"高 PE 标的: {', '.join(names)}（估值偏高，注意风险）")

        return "\n\n".join([f"{i+1}. {a}" for i, a in enumerate(advice)])

    def _build_wechat_summary(self, report: dict) -> str:
        """生成微信摘要"""
        lines = []
        lines.append(f"📋 **盘前报告** {report['date']}")
        lines.append(f"🎯 情绪: **{report['phase']}**")
        lines.append("")

        # 关键指标
        sentiment = report['sentiment']
        m = sentiment['metrics']
        lines.append(f"📊 涨停{m['zt_count']} | 跌停{m['dt_count']} | 炸板{m['zb_count']} (封板率{m['seal_rate']:.0%})")
        lines.append(f"📊 昨日涨停溢价 {m['prev_avg_chg']:+.2f}%")
        lines.append("")

        # 题材
        theme = report['theme']
        if theme['themes']:
            lines.append("🔥 **活跃题材:**")
            for t in theme['themes'][:3]:
                lines.append(f"  {t['name']}: {t['zt_count']}只涨停 最高{t['max_board']}板")

        if theme.get('avoid'):
            lines.append("")
            lines.append(f"🚫 **回避:** {', '.join(theme['avoid'])}")

        # 心法推荐
        if report['wisdom_count'] > 0:
            lines.append("")
            lines.append(f"📚 **心法推荐:** {report['wisdom_count']}篇（详见报告）")

        return "\n".join(lines)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--date', help='指定日期 YYYYMMDD')
    parser.add_argument('--push', action='store_true', help='推送微信')
    args = parser.parse_args()

    gen = DailyReportGenerator()
    result = gen.run(date=args.date, push=args.push)

    print("\n" + "=" * 60)
    print("✅ 盘前报告生成完成")
    print("=" * 60)
