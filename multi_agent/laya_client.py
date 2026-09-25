#!/usr/bin/env python3
"""
laya-mlx 接入层 - C 阶段

功能：
1. 封装 laya-mlx typed-decision API
2. 支持本地运行或远程 API 调用
3. 量化场景：信号分类、情绪评分、决策辅助

限制：
- laya-mlx 需要 Apple Silicon + macOS 14+
- WSL 无法直接运行，需 Mac 部署后通过 HTTP/socket 通信

用法：
    python3 multi_agent/laya_client.py --mode local     # 本地模式（需 Mac）
    python3 multi_agent/laya_client.py --mode remote    # 远程模式（HTTP API）
    python3 multi_agent/laya_client.py --demo           # 演示模式（模拟）
"""

import sys
import os
import json
import argparse
from typing import Dict, Optional, Union
from dataclasses import dataclass

BASE = os.path.dirname(os.path.abspath(__file__))


@dataclass
class LayaConfig:
    """laya-mlx 配置"""
    mode: str = 'demo'          # demo | local | remote
    model_path: str = 'aac6fef/laya-mlx'
    api_endpoint: str = 'http://localhost:8080/laya'
    timeout: int = 10


class LayaClient:
    """
    laya-mlx 客户端
    
    三种模式：
    - demo: 模拟返回（测试用，无需 laya-mlx）
    - local: 本地加载（需 Apple Silicon Mac）
    - remote: HTTP API 调用（需 Mac 部署服务）
    """

    def __init__(self, config: LayaConfig = None):
        self.config = config or LayaConfig()
        self._agent = None

        if self.config.mode == 'local':
            self._init_local()
        elif self.config.mode == 'remote':
            self._init_remote()

    def _init_local(self):
        """本地模式：直接加载 laya-mlx"""
        try:
            import laya_mlx as laya
            self._agent = laya.load(self.config.model_path)
            print(f"✅ laya-mlx 本地模式加载成功: {self.config.model_path}")
        except ImportError:
            print("⚠️ laya-mlx 未安装，回退到 demo 模式")
            self.config.mode = 'demo'
        except Exception as e:
            print(f"⚠️ laya-mlx 加载失败: {e}，回退到 demo 模式")
            self.config.mode = 'demo'

    def _init_remote(self):
        """远程模式：检查 API 可用性"""
        import urllib.request
        try:
            req = urllib.request.Request(
                f"{self.config.api_endpoint}/health",
                method='GET'
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    print(f"✅ laya-mlx 远程 API 可用: {self.config.api_endpoint}")
                else:
                    print(f"⚠️ laya-mlx 远程 API 异常: {resp.status}")
                    self.config.mode = 'demo'
        except Exception as e:
            print(f"⚠️ laya-mlx 远程 API 不可达: {e}")
            print("   提示: 需在 Mac 上部署 laya-server.py")
            self.config.mode = 'demo'

    def predict(
        self,
        state: Union[str, dict],
        questions: dict,
    ) -> dict:
        """
        执行 typed-decision 推理
        
        Args:
            state: 当前状态（文本或结构化数据）
            questions: 问题定义（choice/score/noul）
        
        Returns:
            {"answers": {...}, "confidence": float}
        """
        if self.config.mode == 'local' and self._agent:
            return self._predict_local(state, questions)
        elif self.config.mode == 'remote':
            return self._predict_remote(state, questions)
        else:
            return self._predict_demo(state, questions)

    def _predict_local(self, state, questions) -> dict:
        """本地推理"""
        result = self._agent.predict(state, questions)
        return {
            'answers': result.get('answers', {}),
            'confidence': result.get('confidence', 0.8),
            'mode': 'local',
        }

    def _predict_remote(self, state, questions) -> dict:
        """远程 API 调用"""
        import urllib.request

        payload = json.dumps({
            'state': state,
            'questions': questions,
        }).encode()

        req = urllib.request.Request(
            f"{self.config.api_endpoint}/predict",
            data=payload,
            headers={'Content-Type': 'application/json'},
            method='POST'
        )

        with urllib.request.urlopen(req, timeout=self.config.timeout) as resp:
            result = json.loads(resp.read().decode())
            return {
                'answers': result.get('answers', {}),
                'confidence': result.get('confidence', 0.8),
                'mode': 'remote',
            }

    def _predict_demo(self, state, questions) -> dict:
        """
        Demo 模式：模拟 typed-decision 返回
        用于测试和开发，无需 laya-mlx
        """
        answers = {}
        state_str = json.dumps(state, ensure_ascii=False) if isinstance(state, dict) else str(state)

        for key, q in questions.items():
            q_type = q.get('type', 'choice')

            if q_type == 'choice':
                # 模拟选择：基于关键词匹配
                criteria = q.get('criteria', [])
                if isinstance(criteria, dict):
                    options = list(criteria.keys())
                else:
                    options = criteria if isinstance(criteria, list) else []

                # 简单关键词匹配
                selected = options[0] if options else 'unknown'
                for opt in options:
                    if opt.lower() in state_str.lower():
                        selected = opt
                        break

                answers[key] = {
                    'value': selected,
                    'confidence': 0.75,
                }

            elif q_type == 'score':
                # 模拟评分：基于文本长度/关键词
                criteria = q.get('criteria', [])
                score_idx = min(len(criteria) - 1, len(state_str) // 100) if criteria else 0
                score_idx = max(0, score_idx)

                answers[key] = {
                    'value': criteria[score_idx] if criteria else 'medium',
                    'score': score_idx / max(len(criteria) - 1, 1) if criteria else 0.5,
                    'confidence': 0.7,
                }

            elif q_type == 'noul':
                # 模拟是非判断：关键词匹配
                instructions = q.get('instructions', '').lower()
                value = any(kw in state_str.lower() for kw in ['是', 'yes', 'true', '有', '要'])

                answers[key] = {
                    'value': value,
                    'confidence': 0.8,
                }

        return {
            'answers': answers,
            'confidence': 0.75,
            'mode': 'demo',
        }

    # ------------------------------------------------------------------
    # 量化场景封装
    # ------------------------------------------------------------------
    def classify_signal(self, signal_data: dict) -> dict:
        """
        信号分类：判断信号类型
        
        Args:
            signal_data: {
                'ticker': '600825',
                'name': '新华传媒',
                'signals': ['涨停', '放量', '突破'],
                'context': '出版行业 5连板'
            }
        """
        questions = {
            'signal_type': {
                'type': 'choice',
                'instructions': 'What type of trading signal is this?',
                'criteria': {
                    'breakout': 'price breakout, new high',
                    'reversal': 'trend reversal, bottom fishing',
                    'momentum': 'strong momentum, consecutive gains',
                    'mean_reversion': 'overbought/oversold, pullback',
                    'event_driven': 'news, earnings, policy',
                }
            },
            'strength': {
                'type': 'score',
                'instructions': 'How strong is this signal?',
                'criteria': ['weak', 'moderate', 'strong', 'very strong']
            },
            'is_high_risk': {
                'type': 'noul',
                'instructions': 'Is this a high-risk setup (high position, late stage)?'
            }
        }

        return self.predict(signal_data, questions)

    def score_sentiment(self, market_data: dict) -> dict:
        """
        情绪评分：判断市场情绪状态
        
        Args:
            market_data: {
                'zt_count': 52,
                'dt_count': 13,
                'zb_count': 10,
                'prev_premium': 0.55,
                'max_streak': 5
            }
        """
        questions = {
            'market_phase': {
                'type': 'choice',
                'instructions': 'What is the current market sentiment phase?',
                'criteria': {
                    'ice_point': 'extreme fear, capitulation',
                    'repair': 'recovering from lows, cautious optimism',
                    'rally': 'healthy uptrend, broad participation',
                    'climax': 'euphoria, overbought, distribution',
                    'decline': 'topping, profit taking, risk off',
                }
            },
            'risk_level': {
                'type': 'score',
                'instructions': 'What is the current risk level?',
                'criteria': ['low', 'moderate', 'elevated', 'high', 'extreme']
            },
            'should_enter': {
                'type': 'noul',
                'instructions': 'Is it safe to enter new positions now?'
            }
        }

        return self.predict(market_data, questions)

    def assist_decision(self, portfolio_state: dict) -> dict:
        """
        决策辅助：仓位管理建议
        
        Args:
            portfolio_state: {
                'total_value': 100000,
                'cash_ratio': 0.3,
                'positions': [{'ticker': '600825', 'pnl': 0.15}],
                'market_phase': '修复期'
            }
        """
        questions = {
            'action': {
                'type': 'choice',
                'instructions': 'What portfolio action is most appropriate?',
                'criteria': {
                    'increase': 'add positions, deploy cash',
                    'hold': 'maintain current allocation',
                    'reduce': 'trim positions, raise cash',
                    'hedge': 'add protection, defensive',
                    'exit': 'liquidate, wait for clarity',
                }
            },
            'position_size': {
                'type': 'score',
                'instructions': 'What position size is appropriate?',
                'criteria': ['minimal (0-10%)', 'small (10-30%)', 'moderate (30-60%)', 'full (60-100%)']
            },
            'needs_review': {
                'type': 'noul',
                'instructions': 'Does this portfolio need immediate attention?'
            }
        }

        return self.predict(portfolio_state, questions)


# ----------------------------------------------------------------------
# laya-server.py: 远程服务模式（在 Mac 上运行）
# ----------------------------------------------------------------------
LAYA_SERVER_CODE = '''#!/usr/bin/env python3
"""
laya-mlx HTTP 服务器
在 Apple Silicon Mac 上运行，提供远程 API

用法:
    pip install laya-mlx fastapi uvicorn
    python laya-server.py

API:
    GET  /health          # 健康检查
    POST /predict         # 推理
"""

from fastapi import FastAPI
from pydantic import BaseModel
import laya_mlx as laya

app = FastAPI(title="laya-mlx Server")
agent = laya.load("aac6fef/laya-mlx")


class PredictRequest(BaseModel):
    state: dict | str
    questions: dict


class PredictResponse(BaseModel):
    answers: dict
    confidence: float
    mode: str = "remote"


@app.get("/health")
def health():
    return {"status": "ok", "model": "aac6fef/laya-mlx"}


@app.post("/predict")
def predict(req: PredictRequest):
    result = agent.predict(req.state, req.questions)
    return PredictResponse(
        answers=result.get("answers", {}),
        confidence=result.get("confidence", 0.8),
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
'''


def generate_laya_server(output_path: str = None):
    """生成 laya-server.py 到指定路径"""
    if output_path is None:
        output_path = os.path.join(BASE, 'scripts', 'laya_server.py')

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w') as f:
        f.write(LAYA_SERVER_CODE)

    print(f"✅ laya-server.py 已生成: {output_path}")
    print("   部署: 在 Apple Silicon Mac 上运行")
    print("   用法: pip install laya-mlx fastapi uvicorn && python laya_server.py")
    return output_path


# ----------------------------------------------------------------------
# CLI 测试
# ----------------------------------------------------------------------
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='laya-mlx 客户端')
    parser.add_argument('--mode', choices=['demo', 'local', 'remote'], default='demo')
    parser.add_argument('--endpoint', default='http://localhost:8080/laya')
    parser.add_argument('--demo', action='store_true', help='运行演示')
    parser.add_argument('--generate-server', action='store_true', help='生成 laya-server.py')
    args = parser.parse_args()

    if args.generate_server:
        generate_laya_server()
        sys.exit(0)

    print("=" * 60)
    print("🧠 laya-mlx 客户端")
    print("=" * 60)

    config = LayaConfig(
        mode=args.mode,
        api_endpoint=args.endpoint,
    )
    client = LayaClient(config)

    print(f"\n📊 模式: {client.config.mode}")

    # 演示
    if args.demo or True:
        print("\n" + "=" * 60)
        print("演示 1: 信号分类")
        print("=" * 60)

        signal = {
            'ticker': '600825',
            'name': '新华传媒',
            'signals': ['涨停', '5连板', '放量'],
            'context': '出版行业 空间板 情绪龙头'
        }
        result = client.classify_signal(signal)
        print(json.dumps(result, indent=2, ensure_ascii=False))

        print("\n" + "=" * 60)
        print("演示 2: 情绪评分")
        print("=" * 60)

        market = {
            'zt_count': 52,
            'dt_count': 13,
            'zb_count': 10,
            'prev_premium': 0.55,
            'max_streak': 5
        }
        result = client.score_sentiment(market)
        print(json.dumps(result, indent=2, ensure_ascii=False))

        print("\n" + "=" * 60)
        print("演示 3: 决策辅助")
        print("=" * 60)

        portfolio = {
            'total_value': 50000,
            'cash_ratio': 0.3,
            'positions': [{'ticker': '600825', 'pnl': 0.15}],
            'market_phase': '修复期'
        }
        result = client.assist_decision(portfolio)
        print(json.dumps(result, indent=2, ensure_ascii=False))

    print("\n" + "=" * 60)
    print("✅ 完成")
    print("=" * 60)
