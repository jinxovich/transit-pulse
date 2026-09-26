"""Экспорт GRU-ансамбля в ONNX, проверка паритета и замер латентности onnxruntime.

Граф на выходе отдаёт **абсолютную** задержку в секундах:
``delay_s = cur_dev_s + среднее остатков по моделям``, где ``cur_dev_s`` восстанавливается
из первого статического признака (``static[:, 0] · CUR_DEV_SCALE``). Сервингу нужен
только onnxruntime и функции ``build_sequence``/``sequence_static``.
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import torch
from torch import nn

from services.ml.app.seq_model import TARGET_SCALE, SeqRegressor
from transit_core.sequence import CUR_DEV_SCALE

OPSET = 17
INPUTS = ("seq", "static")
OUTPUT = "delay_s"


class DelayEnsemble(nn.Module):
    """Среднее нескольких ``SeqRegressor`` + подсказка ``cur_dev`` → задержка, сек."""

    def __init__(self, models: list[SeqRegressor]) -> None:
        super().__init__()
        self.models = nn.ModuleList(models)

    def forward(self, seq: torch.Tensor, static: torch.Tensor) -> torch.Tensor:
        resid = torch.stack([m(seq, static) for m in self.models]).mean(0)
        return static[:, 0] * CUR_DEV_SCALE + resid * TARGET_SCALE


def export(models: list[SeqRegressor], example: tuple[np.ndarray, np.ndarray], path: Path):
    """Сохраняет ансамбль в ONNX (opset 17, динамический batch); возвращает CPU-модуль."""
    ens = DelayEnsemble([m.cpu().eval() for m in models]).eval()
    xs, xt = (torch.as_tensor(a[:2], dtype=torch.float32) for a in example)
    path.parent.mkdir(parents=True, exist_ok=True)
    torch.onnx.export(
        ens,
        (xs, xt),
        str(path),
        input_names=list(INPUTS),
        output_names=[OUTPUT],
        dynamic_axes={"seq": {0: "batch"}, "static": {0: "batch"}, OUTPUT: {0: "batch"}},
        opset_version=OPSET,
        dynamo=False,
    )
    return ens


def session(path: Path, threads: int = 1):
    """CPU-сессия onnxruntime для файла модели (``threads=0`` — все ядра по умолчанию)."""
    import onnxruntime as ort

    opts = ort.SessionOptions()
    opts.intra_op_num_threads = threads
    return ort.InferenceSession(str(path), opts, providers=["CPUExecutionProvider"])


def onnx_predict(sess, seq: np.ndarray, static: np.ndarray) -> np.ndarray:
    """Задержка в секундах из ONNX-сессии."""
    feeds = {"seq": seq.astype(np.float32), "static": static.astype(np.float32)}
    return sess.run([OUTPUT], feeds)[0]


@torch.no_grad()
def parity(ens: DelayEnsemble, sess, seq: np.ndarray, static: np.ndarray) -> float:
    """Максимальное абсолютное расхождение torch vs onnxruntime, сек."""
    ref = ens(torch.as_tensor(seq), torch.as_tensor(static)).numpy()
    return float(np.max(np.abs(ref - onnx_predict(sess, seq, static))))


def latency(sess, seq: np.ndarray, static: np.ndarray, batches=(1, 30, 300), reps=50) -> dict:
    """p50/p95 латентности (мс) onnxruntime CPU на батчах заданных размеров."""
    out = {}
    for b in batches:
        idx = np.arange(b) % len(seq)
        xs, xt = seq[idx], static[idx]
        onnx_predict(sess, xs, xt)  # прогрев
        times = []
        for _ in range(reps):
            t0 = time.perf_counter()
            onnx_predict(sess, xs, xt)
            times.append((time.perf_counter() - t0) * 1000)
        out[str(b)] = {
            "p50_ms": round(float(np.percentile(times, 50)), 3),
            "p95_ms": round(float(np.percentile(times, 95)), 3),
        }
    return out


def bench(path: Path, seq: np.ndarray, static: np.ndarray) -> dict:
    """Латентность в двух режимах: один поток и все потоки onnxruntime."""
    return {
        "cpu_1thread": latency(session(path, 1), seq, static),
        "cpu_all_threads": latency(session(path, 0), seq, static),
    }
