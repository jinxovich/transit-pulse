"""GRU-модель остатка задержки: последовательность телеметрии + статика группы A.

Модель предсказывает остаток ``y - cur_dev_s`` (в сотнях секунд), прогноз задержки —
``cur_dev_s + 100·остаток``. Обучение — L1 с весами (синтетика легче реальных ТС).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import torch
from torch import nn

TARGET_SCALE = 100.0


@dataclass(frozen=True)
class TrainConfig:
    """Гиперпараметры обучения (подобраны один раз и зафиксированы до CV)."""

    hidden: int = 64
    layers: int = 2
    dropout: float = 0.1
    static_hidden: int = 32
    epochs: int = 40
    batch: int = 128
    lr: float = 2e-3
    weight_decay: float = 1e-4


DEFAULT_CONFIG = TrainConfig()

class SeqRegressor(nn.Module):
    """GRU по последовательности + MLP по статике → остаток (в единицах ``TARGET_SCALE``)."""

    def __init__(self, n_seq: int, n_static: int, cfg: TrainConfig = DEFAULT_CONFIG) -> None:
        super().__init__()
        self.gru = nn.GRU(
            n_seq, cfg.hidden, num_layers=cfg.layers, batch_first=True, dropout=cfg.dropout
        )
        self.static = nn.Sequential(nn.Linear(n_static, cfg.static_hidden), nn.GELU())
        self.head = nn.Sequential(
            nn.Linear(cfg.hidden + cfg.static_hidden, 64),
            nn.GELU(),
            nn.Dropout(cfg.dropout),
            nn.Linear(64, 1),
        )

    def forward(self, seq: torch.Tensor, static: torch.Tensor) -> torch.Tensor:
        """``seq (B, L, F)``, ``static (B, S)`` → остаток ``(B,)``."""
        _, h = self.gru(seq)
        z = torch.cat([h[-1], self.static(static)], dim=1)
        return self.head(z).squeeze(1)


def device() -> torch.device:
    """CUDA, если доступна, иначе CPU."""
    return torch.device("cuda" if torch.cuda.is_available() else "cpu")


def _tensors(dev: torch.device, *arrays: np.ndarray) -> list[torch.Tensor]:
    return [torch.as_tensor(np.asarray(a, dtype=np.float32), device=dev) for a in arrays]


def fit(
    seq: np.ndarray,
    static: np.ndarray,
    resid: np.ndarray,
    weight: np.ndarray,
    seed: int,
    cfg: TrainConfig = DEFAULT_CONFIG,
) -> SeqRegressor:
    """Обучает модель на остатке ``resid`` (секунды) с весами строк; возвращает eval-модель."""
    torch.manual_seed(seed)
    rng = np.random.default_rng(seed)
    dev = device()
    model = SeqRegressor(seq.shape[2], static.shape[1], cfg).to(dev)
    opt = torch.optim.AdamW(model.parameters(), lr=cfg.lr, weight_decay=cfg.weight_decay)
    steps = cfg.epochs * int(np.ceil(len(resid) / cfg.batch))
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=cfg.lr, total_steps=steps)
    xs, xt, y, w = _tensors(dev, seq, static, resid / TARGET_SCALE, weight)
    model.train()
    for _ in range(cfg.epochs):
        order = torch.as_tensor(rng.permutation(len(y)), device=dev)
        for start in range(0, len(y), cfg.batch):
            idx = order[start : start + cfg.batch]
            loss = (w[idx] * (model(xs[idx], xt[idx]) - y[idx]).abs()).sum() / w[idx].sum()
            opt.zero_grad()
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            sched.step()
    return model.eval()


@torch.no_grad()
def predict(model: SeqRegressor, seq: np.ndarray, static: np.ndarray) -> np.ndarray:
    """Остаток в секундах для батча точек."""
    dev = next(model.parameters()).device
    xs, xt = _tensors(dev, seq, static)
    return model(xs, xt).cpu().numpy().astype(np.float64) * TARGET_SCALE


def fit_predict(
    train: tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray],
    pred: tuple[np.ndarray, np.ndarray],
    seeds: list[int],
    cfg: TrainConfig = DEFAULT_CONFIG,
) -> np.ndarray:
    """Среднее по нескольким seed: снижает дисперсию маленькой сети на малых данных."""
    outs = [predict(fit(*train, seed=s, cfg=cfg), *pred) for s in seeds]
    return np.mean(outs, axis=0)
