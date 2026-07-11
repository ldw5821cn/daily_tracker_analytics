"""
多模型融合预测 - LSTM + LightGBM + XGBoost + RandomForest
运行在 tf_venv 中（通过子进程），4 模型投票融合。
"""
import sys
import os
import json
import subprocess
import tempfile
import shutil
import warnings
warnings.filterwarnings('ignore')

TF_VENV_PYTHON = os.path.expanduser('~/tf_venv/bin/python')
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MULTI_AGENT_DIR = os.path.dirname(SCRIPT_DIR)
TEMPLATE_PATH = os.path.join(SCRIPT_DIR, 'multi_model_predictor_script.py')


def run_in_tf_venv(ticker, days=5, name=""):
    """在 tf_venv 中执行多模型预测"""
    # 读取模板脚本，替换占位符
    with open(TEMPLATE_PATH, 'r', encoding='utf-8') as f:
        script = f.read()
    
    script = script.replace('TICKER_PLACEHOLDER', ticker)
    script = script.replace('NAME_PLACEHOLDER', name or '')
    script = script.replace('MULTI_AGENT_DIR_PLACEHOLDER', MULTI_AGENT_DIR)
    script = script.replace('DAYS_PLACEHOLDER', str(list(range(1, days + 1))))
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8') as f:
        f.write(script)
        script_path = f.name
    
    try:
        result = subprocess.run(
            [TF_VENV_PYTHON, script_path],
            capture_output=True, text=True, timeout=300
        )
    finally:
        os.unlink(script_path)
    
    if result.returncode != 0:
        return {'error': result.stderr[:500]}
    
    for line in reversed(result.stdout.strip().split('\n')):
        line = line.strip()
        if line.startswith('{'):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    
    return {'error': f'无有效JSON: {result.stdout[:200]}'}


def analyze(ticker, name="", days=5):
    return run_in_tf_venv(ticker, days, name)


def format_report(result):
    if 'error' in result:
        return f"❌ {result['error']}"
    
    lines = [f"🧠 多模型融合预测: {result.get('name','')}({result['ticker']})",
             f"   当前价: {result['current_price']} | {result['data_days']}天数据",
             f"   集成判断: {result['ensemble']['direction']} (共识度{result['ensemble']['consensus']}%)",
             ""]
    for dk, models in result.get('results', {}).items():
        lines.append(f"  📅 {dk.replace('day_','')}日后")
        for mn, m in models.items():
            icon = "🟢" if m['dir'] == '涨' else "🔴"
            prob_str = f" (prob={m['prob']})" if 'prob' in m else ""
            lines.append(f"    {icon} {mn.upper():6s}: {m['dir']} | 准确率{m['test_acc']}%{prob_str}")
        lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--ticker', '-t', help='股票代码（默认全部）')
    parser.add_argument('--name', '-n', default='')
    parser.add_argument('--days', '-d', type=int, default=5)
    args = parser.parse_args()
    
    if args.ticker:
        print(format_report(analyze(args.ticker, args.name, args.days)))
    else:
        sys.path.insert(0, MULTI_AGENT_DIR)
        from core.watchlist import get_stocks_as_tuples
        for t, n in get_stocks_as_tuples():
            print(format_report(analyze(t, n, args.days)))
            print()
