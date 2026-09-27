"""Калибровка прогноза по OOF: интервалы по корзинам lead и изотоника для ``p_late``.

Сырые q10/q90 CatBoost MultiQuantile узкие (покрытие ~0.45 вместо 0.8), а ``p_late`` из
кусочно-линейной CDF через квантили нигде не проверялась на калибровку. Здесь:

* метрики качества вероятности (Brier, ECE по 10 корзинам, reliability-таблица) для ``p_late``
  против события ``y > 120 с`` и покрытие/полуширина интервала всего и по корзинам ``lead``;
* split-conformal масштаб интервала на корзину ``lead`` (узкие корзины сливаются с соседями
  до :data:`MIN_BIN_N` точек) — вместо одного общего ``interval_scale``;
* изотоническая регрессия ``p_exceed → P(y > 120)`` поверх интервала с масштабами по lead.

Оценка «после» честная: масштабы и изотоника фитятся на остальных фолдах и применяются к
отложенному (кросс-фит по тем же фолдам CV, что дали OOF). Итоговый артефакт
``models/calibration_{mode}.json`` фитится на всём OOF. Пересчёт по кешу OOF без переобучения::

    uv run python -m services.ml.app.calibration
"""

from __future__ import annotations

import json
import logging
import math
import sys
from pathlib import Path

import numpy as np
from sklearn.isotonic import IsotonicRegression

from services.ml.app.dataset import CACHE, ROOT
from services.ml.app.inference import LATE_S, Calibration, lead_bin_index, p_exceed, widen

VERSION = 1
TARGET_COVERAGE = 0.8
LEAD_EDGES = (10.0, 11.0, 12.0, 13.0, 14.0, 15.0)
MIN_BIN_N = 40
MIN_FIT_N = 10
N_PROB_BINS = 10
MODES = ("submission", "stream")
MODELS = ROOT / "models"
_EPS = 1e-6
log = logging.getLogger("calibration")


# --- метрики -------------------------------------------------------------------------------

def brier(p: np.ndarray, event: np.ndarray) -> float:
    """Brier score: средний квадрат ``p − 1[событие]``."""
    return float(np.mean((np.asarray(p, dtype=float) - np.asarray(event, dtype=float)) ** 2))


def _prob_bins(p: np.ndarray, event: np.ndarray,
               n_bins: int) -> list[tuple[int, float, float, int]]:
    """Корзины равной ширины по p: (номер, средняя p, частота события, n); пустые пропущены."""
    p = np.clip(np.asarray(p, dtype=float), 0.0, 1.0)
    event = np.asarray(event, dtype=float)
    idx = np.minimum((p * n_bins).astype(int), n_bins - 1)
    out = []
    for b in range(n_bins):
        m = idx == b
        if m.any():
            out.append((b, float(p[m].mean()), float(event[m].mean()), int(m.sum())))
    return out


def reliability(p: np.ndarray, event: np.ndarray, n_bins: int = N_PROB_BINS) -> list[dict]:
    """Reliability-таблица: корзина p → средняя p, частота события, число точек."""
    return [{"bin": f"{b / n_bins:.1f}–{(b + 1) / n_bins:.1f}", "p_mean": round(pm, 4),
             "freq": round(fr, 4), "n": n} for b, pm, fr, n in _prob_bins(p, event, n_bins)]


def ece(p: np.ndarray, event: np.ndarray, n_bins: int = N_PROB_BINS) -> float:
    """Expected calibration error: взвешенное по n среднее ``|средняя p − частота|``."""
    rows = _prob_bins(p, event, n_bins)
    total = sum(n for *_, n in rows)
    return float(sum(n * abs(pm - fr) for _, pm, fr, n in rows) / total) if total else 0.0


def _interval_stats(q: np.ndarray, y: np.ndarray, mask: np.ndarray) -> tuple[float, float]:
    """Покрытие [q10, q90] и средняя полуширина на подмножестве строк."""
    q, y = q[mask], y[mask]
    return (float(np.mean((q[:, 0] <= y) & (y <= q[:, 2]))),
            float(np.mean((q[:, 2] - q[:, 0]) / 2)))


# --- корзины lead и масштабы ----------------------------------------------------------------

def lead_bins(lead: np.ndarray, edges: tuple[float, ...] = LEAD_EDGES,
              min_n: int = MIN_BIN_N) -> list[tuple[float, float]]:
    """Корзины ``(lo, hi]`` по lead: слева направо копим соседние, пока не наберётся ``min_n``;
    недобравший хвост сливается с предыдущей корзиной."""
    lead = np.asarray(lead, dtype=float)
    idx = lead_bin_index(lead[~np.isnan(lead)], np.array(edges[1:-1]))
    counts = np.bincount(idx, minlength=len(edges) - 1)
    groups: list[tuple[float, float]] = []
    start, acc = 0, 0
    for i, c in enumerate(counts):
        acc += int(c)
        if acc >= min_n:
            groups.append((float(edges[start]), float(edges[i + 1])))
            start, acc = i + 1, 0
    if start < len(counts):
        lo = groups.pop()[0] if groups else float(edges[0])
        groups.append((lo, float(edges[-1])))
    return groups


def conformal_scale(q: np.ndarray, y: np.ndarray, target: float = TARGET_COVERAGE) -> float:
    """Split-conformal множитель отступов q10/q90 от медианы до покрытия ``target``.

    Оценка несоответствия ``max((q50−y)/(q50−q10), (y−q50)/(q90−q50))``; масштаб — её
    квантиль уровня ``⌈(n+1)·target⌉/n``.
    """
    med = q[:, 1]
    score = np.maximum((med - y) / np.maximum(med - q[:, 0], _EPS),
                       (y - med) / np.maximum(q[:, 2] - med, _EPS))
    n = len(score)
    level = min(1.0, math.ceil((n + 1) * target) / n)
    return float(np.quantile(score, level, method="higher"))


def fit_isotonic(p: np.ndarray, event: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Узлы (x, y) изотонической регрессии ``P(событие | p)`` (неубывающая, в [0, 1])."""
    iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0, increasing=True)
    iso.fit(np.asarray(p, dtype=float), np.asarray(event, dtype=float))
    return iso.X_thresholds_, iso.y_thresholds_


def fit(q: np.ndarray, y: np.ndarray, lead: np.ndarray, bins: list[tuple[float, float]],
        n_repeats: int = 1) -> dict:
    """Артефакт калибровки по строкам OOF: абсолютные сырые квантили ``q`` (n, 3), цель, lead.

    ``n`` в корзинах — число точек (строки / ``n_repeats``).
    """
    cuts = np.array([hi for _, hi in bins[:-1]], dtype=float)
    idx = lead_bin_index(lead, cuts)
    global_scale = conformal_scale(q, y)
    rows = []
    for b, (lo, hi) in enumerate(bins):
        m = idx == b
        s = conformal_scale(q[m], y[m]) if m.sum() >= MIN_FIT_N else global_scale
        rows.append({"lo": lo, "hi": hi, "scale": round(s, 4), "n": int(m.sum() // n_repeats)})
    art = {"version": VERSION, "target_coverage": TARGET_COVERAGE, "late_s": LATE_S,
           "lead_bins": rows, "global_scale": round(global_scale, 4), "err_k": 1.0,
           "isotonic": {"x": [0.0, 1.0], "y": [0.0, 1.0]}}
    qw = widen(q, Calibration.from_dict(art).scale_for(lead))
    iso_x, iso_y = fit_isotonic(p_exceed(qw), y > LATE_S)
    half = (qw[:, 2] - qw[:, 0]) / 2
    art["err_k"] = round(float(np.mean(np.abs(qw[:, 1] - y)) / max(np.mean(half), _EPS)), 4)
    art["isotonic"] = {"x": [float(v) for v in iso_x], "y": [round(float(v), 6) for v in iso_y]}
    return art


# --- оценка до/после ------------------------------------------------------------------------

def _cross_fit(qa: np.ndarray, y: np.ndarray, lead: np.ndarray, folds: np.ndarray,
               bins: list[tuple[float, float]]) -> tuple[np.ndarray, np.ndarray]:
    """Интервалы и p_late «после» для каждой строки OOF: калибровка без её фолда."""
    n_rep, n = folds.shape
    q_out, p_out = np.empty_like(qa), np.empty(len(qa))
    for r in range(n_rep):
        qr = qa[r * n:(r + 1) * n]
        for f in np.unique(folds[r]):
            te = folds[r] == f
            cal = Calibration.from_dict(fit(qr[~te], y[~te], lead[~te], bins))
            rows = r * n + np.flatnonzero(te)
            q_out[rows] = widen(qr[te], cal.scale_for(lead[te]))
            p_out[rows] = cal.p_late(p_exceed(q_out[rows]))
    return q_out, p_out


def _by_lead(q_before, q_after, y, idx, bins, n_repeats) -> tuple[dict, dict]:
    labels = [f"{lo:g}–{hi:g}" for lo, hi in bins]
    counts = [int((idx == b).sum() // n_repeats) for b in range(len(bins))]
    cov = {"bins": labels, "n": counts, "before": [], "after": []}
    half = {"bins": labels, "n": counts, "before": [], "after": []}
    for b in range(len(bins)):
        for key, q in (("before", q_before), ("after", q_after)):
            c, h = _interval_stats(q, y, idx == b)
            cov[key].append(round(c, 4))
            half[key].append(round(h, 2))
    return cov, half


def evaluate(q: np.ndarray, base: np.ndarray, y: np.ndarray, lead: np.ndarray,
             folds: np.ndarray, global_scale: float) -> tuple[dict, dict]:
    """Артефакт калибровки (на всём OOF) и отчёт до/после для ``metrics.json``.

    :param q: OOF-квантили остатка (повтор, точка, 3), как в ``train.catboost_cv``.
    :param folds: номер фолда каждой точки по повторам (повтор, точка).
    :param global_scale: нынешний общий ``interval_scale`` — это состояние «до».
    """
    n_rep = q.shape[0]
    qa = (q + base[None, :, None]).reshape(-1, 3)
    yp, lp = np.tile(y, n_rep), np.tile(lead, n_rep)
    event = yp > LATE_S
    bins = lead_bins(lead)
    art = fit(qa, yp, lp, bins, n_rep)
    q_before = widen(qa, global_scale)
    p_before = p_exceed(q_before)
    q_after, p_after = _cross_fit(qa, y, lead, folds, bins)
    idx = lead_bin_index(lp, np.array([hi for _, hi in bins[:-1]], dtype=float))
    cov, half = _by_lead(q_before, q_after, yp, idx, bins, n_rep)
    all_rows = np.ones(len(yp), dtype=bool)
    (cb, hb), (ca, ha) = _interval_stats(q_before, yp, all_rows), _interval_stats(q_after, yp,
                                                                                    all_rows)
    report = {
        "late_s": LATE_S, "target_coverage": TARGET_COVERAGE, "repeats": n_rep,
        "after_method": "кросс-фит по фолдам CV: split-conformal масштаб по корзине lead + "
                        "изотоника p_late, фит без отложенного фолда",
        "event_rate": round(float(event.mean()), 4),
        "brier_before": round(brier(p_before, event), 5),
        "brier_after": round(brier(p_after, event), 5),
        "ece_before": round(ece(p_before, event), 5),
        "ece_after": round(ece(p_after, event), 5),
        "reliability_before": reliability(p_before, event),
        "reliability_after": reliability(p_after, event),
        "coverage_before": round(cb, 4), "coverage_after": round(ca, 4),
        "half_width_before": round(hb, 2), "half_width_after": round(ha, 2),
        "coverage_by_lead": cov, "half_width_by_lead": half,
        "lead_scales": {lbl: row["scale"] for lbl, row in zip(cov["bins"], art["lead_bins"],
                                                             strict=True)},
        "err_k_calibrated": art["err_k"],
    }
    art["quality"] = {k: report[k] for k in ("brier_before", "brier_after", "ece_before",
                                             "ece_after", "coverage_after", "half_width_after")}
    return art, report


# --- запуск ---------------------------------------------------------------------------------

def write_artifact(mode: str, art: dict, models_dir: Path = MODELS) -> Path:
    """``calibration_{mode}.json`` рядом с моделью."""
    path = models_dir / f"calibration_{mode}.json"
    path.write_text(json.dumps({"mode": mode, **art}, ensure_ascii=False, indent=2), "utf-8")
    return path


def main() -> int:
    """Пересчёт калибровки по кешу OOF (``data/cache/oof_{mode}.npz`` от ``train``)."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    metrics_path = MODELS / "metrics.json"
    metrics = json.loads(metrics_path.read_text("utf-8"))
    for mode in MODES:
        path = CACHE / f"oof_{mode}.npz"
        if not path.exists():
            log.warning("%s: нет %s — сначала `python -m services.ml.app.train`", mode, path)
            continue
        d = np.load(path)
        old = metrics[mode]["calibration"]
        art, report = evaluate(d["q"], d["base"], d["y"], d["lead"], d["folds"],
                               float(old["interval_scale"]))
        metrics[mode]["calibration"] = {**old, **report}
        write_artifact(mode, art)
        log.info("%s: brier %.4f→%.4f, ece %.4f→%.4f, покрытие по lead %s", mode,
                 report["brier_before"], report["brier_after"], report["ece_before"],
                 report["ece_after"], report["coverage_by_lead"]["after"])
    metrics_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2, default=float),
                            "utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
