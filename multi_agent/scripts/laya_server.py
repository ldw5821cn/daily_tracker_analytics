#!/usr/bin/env python3
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
