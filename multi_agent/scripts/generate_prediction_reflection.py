#!/usr/bin/env python3
"""每日预测反思：基于验证结果和错误分析，用 LLM 生成策略改进建议并记录。"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
MULTI_AGENT = os.path.join(PROJECT_ROOT, 'multi_agent')
sys.path.insert(0, MULTI_AGENT)
sys.path.insert(0, PROJECT_ROOT)

from core.llm_client import chat

DATA_DIR = os.path.join(MULTI_AGENT, 'data')
VALIDATION_PATH = os.path.join(DATA_DIR, 'morning_validation.json')
ERROR_ANALYSIS_PATH = os.path.join(DATA_DIR, 'prediction_error_analysis.json')
AB_TEST_PATH = os.path.join(DATA_DIR, 'ab_test_signal_changes.json')
REFLECTION_PATH = os.path.join(DATA_DIR, 'prediction_reflection.json')
HISTORY_PATH = os.path.join(DATA_DIR, 'prediction_reflection_history.jsonl')


def _load_json(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def _load_history(limit: int = 7) -> list:
    if not os.path.exists(HISTORY_PATH):
        return []
    items = []
    with open(HISTORY_PATH, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                items.append(json.loads(line))
            except Exception:
                continue
    return items[-limit:]


def _format_error_analysis(error_analysis: dict) -> str:
    """把错误分析 JSON 转成自然语言，避免 LLM API 对中文 JSON 返回空。"""
    summary = error_analysis.get('summary', {})
    lines = [
        f"预测日期: {error_analysis.get('pred_date', '未知')}",
        f"总体准确率: {summary.get('accuracy', 0)}% ({summary.get('correct', 0)}/{summary.get('total', 0)})",
        f"总错误数: {summary.get('wrong', 0)}",
    ]
    by_signal = error_analysis.get('by_signal', {})
    if by_signal:
        lines.append("按信号方向错误率:")
        for signal, d in by_signal.items():
            lines.append(f"  {signal}: 共{d.get('total',0)}条，错{d.get('wrong',0)}条，错误率{d.get('error_rate',0)}%")
    by_category = error_analysis.get('by_category', {})
    if by_category:
        lines.append("按资产类别错误率:")
        for cat, d in by_category.items():
            lines.append(f"  {cat}: 共{d.get('total',0)}条，错{d.get('wrong',0)}条，错误率{d.get('error_rate',0)}%")
    feature_compare = error_analysis.get('feature_compare', {})
    if feature_compare:
        lines.append("预测特征对比（正确 vs 错误样本平均）:")
        for k, v in feature_compare.items():
            if isinstance(v, dict):
                lines.append(f"  {k}: 正确{v.get('correct_avg',0):.2f} vs 错误{v.get('wrong_avg',0):.2f}")
    component_compare = error_analysis.get('component_compare', {})
    if component_compare:
        lines.append("分项得分对比（正确 vs 错误样本平均）:")
        for k, v in component_compare.items():
            if isinstance(v, dict):
                lines.append(f"  {k}: 正确{v.get('correct_avg',0):.2f} vs 错误{v.get('wrong_avg',0):.2f}")
    divergence = error_analysis.get('divergence_analysis', {})
    if divergence:
        lines.append("因子背离度分析:")
        for k, v in divergence.items():
            if isinstance(v, dict):
                lines.append(f"  {k}: 正确样本平均{v.get('correct_avg', 0):.2f} vs 错误样本平均{v.get('wrong_avg', 0):.2f}")
                by_sig = v.get('by_wrong_signal', {})
                if by_sig:
                    lines.append("  按错误信号细分:")
                    for sig, d in by_sig.items():
                        lines.append(f"    {sig}: 平均背离{d.get('avg', 0):.2f} (n={d.get('n', 0)})")
    suggestions = error_analysis.get('suggestions', [])
    if suggestions:
        lines.append("规则分析建议:")
        for s in suggestions:
            lines.append(f"  - {s}")
    return '\n'.join(lines)


def _format_top_samples(samples: list, label: str, max_items: int = 5) -> str:
    if not samples:
        return f"{label}: 无"
    lines = [f"{label} (Top {min(len(samples), max_items)}):"]
    for s in samples[:max_items]:
        lines.append(
            f"  {s.get('ticker','')} {s.get('name','')} | "
            f"信号{s.get('signal','')} | 收益{s.get('return_pct',0):+.2f}% | "
            f"评分{s.get('weighted_score',0):.1f} | 置信{s.get('confidence',0):.2f} | "
            f"原因: {s.get('reasoning','')[:120]}"
        )
    return '\n'.join(lines)


def _build_prompt(validation: dict, error_analysis: dict, history: list, ab_test: dict) -> str:
    pred_date = error_analysis.get('pred_date', validation.get('pred_date', '未知'))
    analysis_text = _format_error_analysis(error_analysis)
    wrong_top10 = error_analysis.get('wrong_top10', [])
    correct_top10 = error_analysis.get('correct_top10', [])

    history_text = ""
    if history:
        history_text = "\n\n===== 近7日反思历史 =====\n"
        for h in history:
            history_text += f"\n日期: {h.get('pred_date', '')}\n"
            history_text += f"准确率: {h.get('accuracy', '')}\n"
            history_text += f"关键改进建议: {h.get('key_suggestions', '')}\n"

    pattern_kb = _load_json(os.path.join(DATA_DIR, 'error_pattern_kb.json'))
    active_patterns = [p for p in pattern_kb.get('patterns', []) if p.get('status') in ('fixed', 'monitoring')]
    if active_patterns:
        history_text += "\n\n===== 已沉淀的错误模式知识库 =====\n"
        for p in active_patterns:
            history_text += f"\n[{p.get('status', '').upper()}] {p.get('id')}\n"
            history_text += f"描述: {p.get('description', '')}\n"
            history_text += f"教训: {p.get('lesson', '')}\n"

    ab_text = ""
    if ab_test:
        ab_text = f"""\n\n===== 自动调参 A/B 测试信号变化 =====\n预测日期: {ab_test.get('pred_date')}
宏观信号: {ab_test.get('macro_signal')}/{ab_test.get('macro_score')}
旧信号分布: {ab_test.get('old_counts', {})}
新信号分布: {ab_test.get('new_counts', {})}
信号迁移: {ab_test.get('transitions', {})}
发生变化标的数: {len(ab_test.get('changed', []))}
"""

    prompt = f"""你是量化投资系统的策略反思 Agent。请基于以下预测验证结果，写一份深度复盘报告。

===== 预测与验证摘要 =====
{analysis_text}

{_format_top_samples(wrong_top10, '失败样本')}

{_format_top_samples(correct_top10, '成功样本')}
{ab_text}
{history_text}

===== 输出要求 =====
请用中文输出，格式如下：

1. 一句话总结：当日预测准确率的本质原因（50字以内）
2. 主要错误模式：列出2-3个导致错误的核心模式
3. 成功信号特征：总结正确样本的共性
4. 数据驱动改进建议：建议扩展哪些历史数据、增加哪些因子或特征，让 parameter_optimizer 自动学习权重/阈值；禁止建议任何硬编码拦截规则、固定阈值或方向否决门。
5. 策略回测建议：基于当前市场状态，6种策略中哪种更值得采用，是否需要调整推荐策略的打分权重
6. 今日操作建议：基于反思对当前持仓或明日预测给出1-2条具体建议
7. 需要新增/改进的数据源或因子：指出当前系统缺少哪些信号

重要约束：
- 本系统坚持数据驱动，拒绝硬编码规则。当信号质量差时，正确做法是获取更多数据、建立数据仓库、积累真实收益标签，让优化器自动学习阈值和权重，而不是在代码中加门控。
- 参考顶级投资研究框架：bull case 总是容易写，要额外关注 bear case；每个 investment thesis 必须有可证伪的 thesis breakers（具体指标/事件阈值，一旦触发就重新评估）。请在分析错误看多/看空信号时，主动写出它们被证伪的方式和应监控的触发器。
- 结合 A 股市场特色做归因（参考资深 A 股分析师视角）：a) 政策市特征——行业政策/监管变化常主导板块短期走势；b) 散户主导——情绪羊群效应放大涨跌，注意龙虎榜/涨停家数/炸板率的散户行为信号；c) 涨跌停与 T+1 制度——涨停封板资金、跌停承接、次日溢价都影响实际可交易收益；d) 北向资金/两融余额/期权 PCR 反映增量资金与风险偏好。分析错误样本时，用这些 A 股特有维度解释"为什么看错"，而不是笼统归因。
- 数据驱动归因优先：先看错误样本在技术/资金/情绪/宏观分项上的量化差异（divergence、component_compare），再结合 A 股特色提出可验证的因子假设，严禁凭空猜测。"""
    return prompt


def generate_reflection():
    validation = _load_json(VALIDATION_PATH)
    error_analysis = _load_json(ERROR_ANALYSIS_PATH)
    ab_test = _load_json(AB_TEST_PATH)
    if not error_analysis:
        print('❌ 未找到错误分析数据，请先运行 analyze_prediction_errors.py')
        sys.exit(1)

    history = _load_history()
    prompt = _build_prompt(validation, error_analysis, history, ab_test)

    print('[reflection] 正在调用 LLM 生成反思...')
    llm_output = chat([
        {'role': 'system', 'content': '你是顶级量化策略师，擅长从预测错误中提炼可执行的策略改进。'},
        {'role': 'user', 'content': prompt},
    ], temperature=0.3, max_tokens=2000)

    if not llm_output:
        print('⚠️ LLM 未返回内容，将保存数据驱动的 fallback 反思。')
        suggestions = error_analysis.get('suggestions', [])
        by_signal = error_analysis.get('by_signal', {})
        by_category = error_analysis.get('by_category', {})
        worst_signal = ''
        worst_rate = 0
        for sig, d in by_signal.items():
            rate = d.get('error_rate', 0) or 0
            if rate > worst_rate:
                worst_rate = rate
                worst_signal = sig
        worst_cat = ''
        worst_cat_rate = 0
        for cat, d in by_category.items():
            rate = d.get('error_rate', 0) or 0
            if rate > worst_cat_rate:
                worst_cat_rate = rate
                worst_cat = cat
        fc = error_analysis.get('feature_compare', {})
        comp = error_analysis.get('component_compare', {})
        divergence = error_analysis.get('divergence_analysis', {})

        def _gap(metric):
            v = fc.get(metric) or comp.get(metric) or divergence.get(metric)
            if isinstance(v, dict):
                return v.get('wrong_avg', 0) - v.get('correct_avg', 0)
            return 0

        gaps = {
            'weighted_score': _gap('weighted_score'),
            'confidence': _gap('confidence'),
            'technical': _gap('技术面') or _gap('technical'),
            'sentiment': _gap('新闻情绪') or _gap('sentiment'),
            'fundamental': _gap('基本面') or _gap('fundamental'),
        }
        sorted_gaps = sorted(gaps.items(), key=lambda x: abs(x[1]), reverse=True)
        fallback_lines = [
            f"# LLM 调用失败，fallback 数据驱动反思（{datetime.now().strftime('%Y-%m-%d %H:%M')}）",
            f"",
            f"## 一句话总结",
            f"当日准确率 {summary.get('accuracy', 0)}%，核心短板是 {worst_signal or '未知信号'} 错误率 {worst_rate:.1f}%，",
            f"{worst_cat or '某类别'} 错误率 {worst_cat_rate:.1f}%；样本特征分化最大在 {sorted_gaps[0][0]}（差距 {sorted_gaps[0][1]:.2f}）。",
            f"",
            f"## 数据驱动改进方向（禁止硬编码阈值）",
            f"1. 将 {worst_signal or '错误率最高信号'} 的失败样本加入 optimizer 的真实收益标签，",
            f"   让 parameter_optimizer_v4 自动学习该信号在何种 feature/regime 组合下应当被降级。",
            f"2. 扩展 {worst_cat or '错误率最高类别'} 的历史数据覆盖（至少 20 个交易日可评估样本），",
            f"   目前样本不足，硬改阈值会导致过拟合。",
        ]
        if sorted_gaps:
            name, gap = sorted_gaps[0]
            fallback_lines += [
                f"3. 针对 '{name}' 正确/错误样本差距（{gap:+.2f}）设计可回测特征：",
                f"   - 将该指标按市场状态（regime）分组，写入 market_regime_features；",
                f"   - 用 5 日 forward return + 交易成本作为标签，让优化器学习该指标在不同 regime 下的权重。",
            ]
        if suggestions:
            fallback_lines += [
                f"4. 规则分析引擎已生成候选建议，但暂不落地为硬编码：{suggestions[:3]}。",
                f"   这些建议将转化为 optimizer 搜索空间中的可选 feature/penalty，由数据验证后生效。",
            ]
        fallback_lines += [
            f"",
            f"## 下一步可证伪假设",
            f"- 假设：{worst_signal or '错误信号'} 在 macro_score 偏{'低' if worst_signal == '看多' else '高'} 时更容易错。",
            f"- 验证：将 macro_score × {worst_signal or '信号'} 交叉特征加入 feature_snapshot，",
            f"  待 8/31 前后 optimizer 样本达标后自动评估该交叉项的预测力。",
        ]
        llm_output = '\n'.join(fallback_lines)

    summary = error_analysis.get('summary', {})
    reflection = {
        'pred_date': error_analysis.get('pred_date'),
        'validation_date': error_analysis.get('validation_date'),
        'accuracy': summary.get('accuracy'),
        'total': summary.get('total'),
        'correct': summary.get('correct'),
        'wrong': summary.get('wrong'),
        'by_signal': error_analysis.get('by_signal', {}),
        'by_category': error_analysis.get('by_category', {}),
        'feature_compare': error_analysis.get('feature_compare', {}),
        'component_compare': error_analysis.get('component_compare', {}),
        'divergence_analysis': error_analysis.get('divergence_analysis', {}),
        'llm_reflection': llm_output,
        'key_suggestions': error_analysis.get('suggestions', []),
        'generated_at': datetime.now().isoformat(),
    }

    # 保存最新反思
    with open(REFLECTION_PATH, 'w', encoding='utf-8') as f:
        json.dump(reflection, f, ensure_ascii=False, indent=2)

    # 追加历史
    with open(HISTORY_PATH, 'a', encoding='utf-8') as f:
        f.write(json.dumps(reflection, ensure_ascii=False) + '\n')

    print(f'✅ 反思已保存: {REFLECTION_PATH}')
    print(f'   历史追加: {HISTORY_PATH}')
    print(f'\n===== LLM 反思摘要 =====\n{llm_output[:500]}...')
    return reflection


if __name__ == '__main__':
    generate_reflection()
