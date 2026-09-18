#!/usr/bin/env python3
"""每日反思优化器：把 morning_validation 的验证结果转化为系统参数微调 + 知识库沉淀。

闭环：每日反思 -> 准确率/错误模式 -> 微调 predictor_params.json 阈值/权重 -> 记录优化历史 + 沉淀经验到知识库。

设计原则：
- 不硬编码：路径从配置/环境读，类别/阈值/微调幅度全部在 config/reflection_optimizer.json 可调
- 单日样本，微调幅度小（阈值 ±2、权重 ±0.03），避免过拟合单日噪声
- 看多/看空分支准确率与阈值反向调整：某分支错误率过高 -> 提高该分支信号门槛
- 每次修改写 updated_reason，可追溯
"""
import json
import os
from datetime import datetime

# ---------- 路径解析（不硬编码，__file__ 推导） ----------
_SCRIPTS = os.path.dirname(os.path.abspath(__file__))          # multi_agent/scripts/
_MULTI = os.path.dirname(_SCRIPTS)                              # multi_agent/
REPO = os.path.dirname(_MULTI)                                  # repo 根
MV = os.path.join(_MULTI, 'data', 'morning_validation.json')
PARAMS = os.path.join(_MULTI, 'config', 'predictor_params.json')
OPT_CONF = os.path.join(_MULTI, 'config', 'reflection_optimizer.json')
OPT_HISTORY = os.path.join(_MULTI, 'data', 'reflection_optimization_history.jsonl')
KNOWLEDGE = os.path.join(_MULTI, 'data', 'strategy_knowledge.json')

# ---------- 默认配置（config/reflection_optimizer.json 不存在时用，且可被该文件覆盖） ----------
DEFAULT_CONF = {
    # 参与优化的预测类别（与 predictor_params.json 的键对应）
    'categories': ['个股', 'ETF', '期货', '_default'],
    # 需要按类别独立微调低准确率的高风险类别
    'low_acc_categories': ['个股'],
    # 触发类别级收紧的准确率下限（%）
    'category_acc_floor': 48,
    # 看多/看空分支的样本量下限（至少这么多样本才调整，避免小样本过拟合）
    'signal_min_samples': 5,
    # 看多分支准确率低于此值（小数）视为极端反向
    'bull_extreme_reverse': 0.20,
    # 看空分支准确率低于此值（小数）视为反向
    'bear_extreme_reverse': 0.25,
    # 单次微调幅度上限
    'max_weight_delta': 0.03,
    'max_thresh_delta': 2,
    # 极强反向（看多准确率<10%）的加强系数
    'extreme_boost_factor': 1.5,
    # 阈值硬边界（防止调崩）
    'thresh_bounds': {'bull': [52, 70], 'strong_bull': [60, 80],
                      'bear': [30, 55], 'strong_bear': [20, 45]},
    # 权重硬边界
    'weight_bounds': [0.10, 0.40],
    # 总体准确率低于此值（%）时沉淀"建议降仓"经验
    'low_overall_acc': 45,
    # 知识库 lessons 保留条数
    'max_lessons': 100,
}


def _load_conf():
    conf = dict(DEFAULT_CONF)
    if os.path.exists(OPT_CONF):
        try:
            with open(OPT_CONF) as f:
                user = json.load(f)
            conf.update(user)  # 用户配置覆盖默认值
        except Exception:
            pass
    return conf


def _load_mv():
    if not os.path.exists(MV):
        return None
    with open(MV) as f:
        return json.load(f)


def _load_params():
    with open(PARAMS) as f:
        return json.load(f)


def _save_params(p):
    p['updated_at'] = datetime.now().isoformat()
    with open(PARAMS, 'w', encoding='utf-8') as f:
        json.dump(p, f, ensure_ascii=False, indent=2)


def _append_history(entry):
    os.makedirs(os.path.dirname(OPT_HISTORY), exist_ok=True)
    with open(OPT_HISTORY, 'a', encoding='utf-8') as f:
        f.write(json.dumps(entry, ensure_ascii=False) + '\n')


def _load_knowledge():
    if not os.path.exists(KNOWLEDGE):
        return {'version': 1, 'lessons': [], 'patterns': []}
    with open(KNOWLEDGE) as f:
        return json.load(f)


def _save_knowledge(kb):
    kb['last_updated'] = datetime.now().isoformat()
    with open(KNOWLEDGE, 'w', encoding='utf-8') as f:
        json.dump(kb, f, ensure_ascii=False, indent=2)


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _already_applied(validate_date: str) -> bool:
    """检查某 validate_date 是否已应用过参数微调（防止手动+自动双跑叠加）。"""
    if not validate_date or not os.path.exists(OPT_HISTORY):
        return False
    try:
        with open(OPT_HISTORY) as f:
            for line in f:
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                if e.get('validate_date') == validate_date and e.get('applied'):
                    return True
    except Exception:
        return False
    return False


def optimize():
    """主入口：读验证结果 -> 微调参数 + 沉淀经验。返回优化报告 dict。"""
    conf = _load_conf()
    mv = _load_mv()
    if not mv:
        return {'applied': False, 'reason': 'morning_validation.json 不存在'}

    overall = mv.get('overall') or {}
    acc = overall.get('accuracy')
    total = overall.get('total', 0)
    if acc is None or total < 30:
        return {'applied': False, 'reason': f'样本不足(total={total})或准确率缺失，跳过微调'}

    by_cat = mv.get('by_category') or {}
    by_sig = mv.get('by_signal') or {}
    params = _load_params()

    # 去重：同一 validate_date 只应用一次参数微调（知识库沉淀仍执行，但不再重复调参）
    vdate = mv.get('validate_date')
    if _already_applied(vdate):
        return {'applied': False, 'reason': f'validate_date={vdate} 已应用过参数微调，跳过（防重复）',
                'overall_accuracy': acc}

    categories = conf['categories']
    max_td = conf['max_thresh_delta']
    max_wd = conf['max_weight_delta']
    tb = conf['thresh_bounds']
    wb = conf['weight_bounds']
    changes = []
    param_changes = []

    # ---------- 1. 按类别微调（低准确率的高风险类别收紧强信号门槛） ----------
    for cat in conf['low_acc_categories']:
        cat_acc = (by_cat.get(cat) or {}).get('accuracy')
        if cat_acc is None or cat not in params:
            continue
        node = params[cat]
        th = node.setdefault('threshold', {})
        before_sb, before_ss = th.get('strong_bull'), th.get('strong_bear')
        if cat_acc < conf['category_acc_floor']:
            th['strong_bull'] = _clamp(int(th.get('strong_bull', 65)) + 1,
                                       tb['strong_bull'][0], tb['strong_bull'][1])
            th['strong_bear'] = _clamp(int(th.get('strong_bear', 35)) - 1,
                                       tb['strong_bear'][0], tb['strong_bear'][1])
            changes.append(f"⚠️ {cat}准确率 {cat_acc:.1f}% < {conf['category_acc_floor']}%：收紧强信号门槛 "
                           f"(strong_bull {before_sb}→{th['strong_bull']}, strong_bear {before_ss}→{th['strong_bear']})")
            param_changes.append(f"{cat}低准确率收紧强信号({cat_acc:.0f}%)")

    # ---------- 2. 看多分支极端反向 ----------
    bull = by_sig.get('看多') or {}
    bull_acc = bull.get('accuracy')
    bull_total = bull.get('total', 0)
    min_s = conf['signal_min_samples']
    if bull_total >= min_s and bull_acc is not None and bull_acc < conf['bull_extreme_reverse'] * 100:
        boost = conf['extreme_boost_factor'] if bull_acc < 10 else 1.0
        delta = round(max_td * boost)
        for cat in categories:
            node = params.get(cat)
            if not node:
                continue
            th = node.setdefault('threshold', {})
            th['bull'] = _clamp(int(th.get('bull', 58)) + delta,
                                tb['bull'][0], tb['bull'][1])
            th['strong_bull'] = _clamp(int(th.get('strong_bull', 65)) + delta,
                                       tb['strong_bull'][0], tb['strong_bull'][1])
        # 降低 debate 权重（多空辩论是看多误判噪声源之一）
        for cat in conf['low_acc_categories']:
            node = params.get(cat)
            if node and 'weights' in node:
                bw = node['weights'].get('debate', 0.25)
                node['weights']['debate'] = round(
                    _clamp(bw - max_wd, wb[0], wb[1]), 3)
        changes.append(f"🔴 看多分支准确率 {bull_acc:.1f}%（{bull.get('correct')}/{bull_total}）"
                       f" 极端反向：全类别 bull/strong_bull +{delta}，"
                       f"{conf['low_acc_categories']} debate 权重 -{max_wd}")
        param_changes.append(f"看多反向提门槛(bull_acc={bull_acc:.0f}%)")

    # ---------- 3. 看空分支过热反向 ----------
    bear = by_sig.get('看空') or {}
    bear_acc = bear.get('accuracy')
    bear_total = bear.get('total', 0)
    if bear_total >= min_s and bear_acc is not None and bear_acc < conf['bear_extreme_reverse'] * 100:
        for cat in categories:
            node = params.get(cat)
            if not node:
                continue
            th = node.setdefault('threshold', {})
            th['bear'] = _clamp(int(th.get('bear', 42)) - max_td,
                                tb['bear'][0], tb['bear'][1])
            th['strong_bear'] = _clamp(int(th.get('strong_bear', 35)) - max_td,
                                       tb['strong_bear'][0], tb['strong_bear'][1])
        changes.append(f"🟢 看空分支准确率 {bear_acc:.1f}%（{bear.get('correct')}/{bear_total}）"
                       f" 反向：全类别 bear/strong_bear -{max_td}")
        param_changes.append(f"看空反向降门槛(bear_acc={bear_acc:.0f}%)")

    # ---------- 4. 写回参数 + 记录 ----------
    applied = bool(changes)
    if applied:
        # 用独立字段 reflection_reason，避免与 auto_tune/warehouse 优化器的 updated_reason 语义混淆
        params['reflection_reason'] = ' | '.join(param_changes)[:500]
        params['reflection_updated_at'] = datetime.now().isoformat()
        _save_params(params)

    entry = {
        'time': datetime.now().isoformat(),
        'validate_date': mv.get('validate_date'),
        'pred_date': mv.get('pred_date'),
        'overall_accuracy': acc,
        'by_category': {k: (v or {}).get('accuracy') for k, v in by_cat.items()},
        'by_signal': {k: (v or {}).get('accuracy') for k, v in by_sig.items()},
        'applied': applied,
        'changes': changes,
    }
    _append_history(entry)

    # ---------- 5. 沉淀经验到知识库 ----------
    kb = _load_knowledge()
    lessons = kb.setdefault('lessons', [])
    stamp = mv.get('validate_date') or datetime.now().strftime('%Y-%m-%d')
    if bull_total >= min_s and bull_acc is not None and bull_acc < conf['bull_extreme_reverse'] * 100:
        lessons.append(f"[{stamp}] 看多信号准确率仅 {bull_acc:.0f}%（{bull.get('correct')}/{bull_total}），"
                       f"看多分支系统性反向，已提高看多门槛 —— 系统性看多误判是主要亏损源")
    if acc is not None and acc < conf['low_overall_acc']:
        lessons.append(f"[{stamp}] 当日总体准确率 {acc:.1f}% 偏低，市场可能处于转折/震荡，建议降低仓位或暂停开新仓")
    kb['lessons'] = lessons[-conf['max_lessons']:]
    _save_knowledge(kb)

    return {
        'applied': applied,
        'overall_accuracy': acc,
        'changes': changes,
        'history': OPT_HISTORY,
        'conf_source': OPT_CONF if os.path.exists(OPT_CONF) else 'DEFAULT_CONF',
    }


if __name__ == '__main__':
    r = optimize()
    print(json.dumps(r, ensure_ascii=False, indent=2))
