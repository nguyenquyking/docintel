"""Statistics the eval and monitoring rely on. Small, dependency-free, unit-tested."""
from __future__ import annotations

import math
import random
from collections.abc import Sequence


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a proportion — honest at small n and near 0/1."""
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def mcnemar_exact(b: int, c: int) -> float:
    """b = baseline right & candidate wrong, c = the reverse. Two-sided exact binomial test:
    under H0 each discordant document is a fair coin. Only discordant pairs carry signal,
    which is why a paired comparison detects regressions a two-accuracy comparison misses."""
    n = b + c
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, i) for i in range(min(b, c) + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def paired_bootstrap(base: Sequence[bool], cand: Sequence[bool], iters: int = 2000, alpha: float = 0.05,
                     seed: int = 0) -> tuple[float, float]:
    """CI of accuracy(cand) − accuracy(base), resampling documents (not systems)."""
    n = len(base)
    if n == 0:
        return (0.0, 0.0)
    rng = random.Random(seed)
    diffs = [int(c) - int(b) for b, c in zip(base, cand, strict=True)]
    deltas = sorted(sum(diffs[rng.randrange(n)] for _ in range(n)) / n for _ in range(iters))
    return deltas[int(iters * alpha / 2)], deltas[int(iters * (1 - alpha / 2)) - 1]


def psi(expected: dict[str, float], actual: dict[str, float], eps: float = 1e-4) -> float:
    """Population stability index between two categorical distributions (shares).
    Rule of thumb: < 0.1 stable, 0.1–0.25 moderate shift, > 0.25 significant shift."""
    keys = set(expected) | set(actual)
    total = 0.0
    for k in keys:
        e = max(expected.get(k, 0.0), eps)
        a = max(actual.get(k, 0.0), eps)
        total += (a - e) * math.log(a / e)
    return total


def shares(values: Sequence) -> dict[str, float]:
    n = len(values)
    out: dict[str, float] = {}
    for v in values:
        out[str(v)] = out.get(str(v), 0.0) + 1.0 / n
    return out


def reliability(points: Sequence[tuple[float, bool]], bins: int = 10) -> tuple[float, list[dict]]:
    """Expected calibration error and the reliability diagram data (confidence vs accuracy)."""
    buckets: list[list[tuple[float, bool]]] = [[] for _ in range(bins)]
    for conf, ok in points:
        buckets[min(bins - 1, int(conf * bins))].append((conf, ok))
    n = len(points) or 1
    ece, out = 0.0, []
    for i, b in enumerate(buckets):
        if not b:
            continue
        mean_conf = sum(c for c, _ in b) / len(b)
        acc = sum(ok for _, ok in b) / len(b)
        ece += len(b) / n * abs(mean_conf - acc)
        out.append({"bin": f"{i / bins:.1f}-{(i + 1) / bins:.1f}", "confidence": round(mean_conf, 4),
                    "accuracy": round(acc, 4), "n": len(b)})
    return round(ece, 4), out


def percentile(values: Sequence[float], q: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    k = (len(s) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def half_width(p: float, n: int, z: float = 1.96) -> float:
    """± margin of a proportion at sample size n — how big a golden set must be."""
    return z * math.sqrt(p * (1 - p) / n) if n else 1.0
