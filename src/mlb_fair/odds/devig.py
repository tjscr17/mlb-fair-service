"""De-vigging: convert a two-way moneyline into fair win probabilities.

American odds -> implied probability -> remove the overround. Three methods,
selectable by config:

  multiplicative : normalize implied probs to sum to 1 (default; standard for
                   2-way, low-hold books like Pinnacle).
  additive       : subtract an equal share of the overround from each side.
  shin           : Shin (1992) — models a proportion of insider money, reducing
                   favorite-longshot bias. Solved numerically (monotone bisection).

For a 2-way low-hold book the three differ by only tens of bps, but additive and
Shin shave the favorite bias that multiplicative leaves in. Method is pluggable.
"""

from __future__ import annotations

import math

from ..models import DevigFair

_EPS = 1e-9


def american_to_prob(price: float) -> float:
    """American odds -> implied probability (includes vig)."""
    if price == 0:
        raise ValueError("american odds cannot be 0")
    if price > 0:
        return 100.0 / (price + 100.0)
    return (-price) / ((-price) + 100.0)


def _clamp(p: float) -> float:
    return min(1.0 - _EPS, max(_EPS, p))


def _multiplicative(ih: float, ia: float) -> tuple[float, float]:
    total = ih + ia
    return ih / total, ia / total


def _additive(ih: float, ia: float) -> tuple[float, float]:
    overround = ih + ia - 1.0
    ph = ih - overround / 2.0
    pa = ia - overround / 2.0
    # extreme favorites can push a side <0; clamp then renormalize
    ph, pa = _clamp(ph), _clamp(pa)
    s = ph + pa
    return ph / s, pa / s


def _shin(ih: float, ia: float) -> tuple[float, float]:
    """Shin's method via bisection on the insider proportion z."""
    book = ih + ia  # B = sum of implied probs (> 1)

    def p_of_z(imp: float, z: float) -> float:
        return (math.sqrt(z * z + 4.0 * (1.0 - z) * imp * imp / book) - z) / (2.0 * (1.0 - z))

    def total(z: float) -> float:
        return p_of_z(ih, z) + p_of_z(ia, z)

    lo, hi = 0.0, 0.999
    # total(0) = sqrt(book) > 1; total decreases in z. Bracket then bisect.
    if total(hi) > 1.0:
        # couldn't bracket (degenerate) -> fall back to multiplicative
        return _multiplicative(ih, ia)
    for _ in range(80):
        mid = (lo + hi) / 2.0
        if total(mid) > 1.0:
            lo = mid
        else:
            hi = mid
    z = (lo + hi) / 2.0
    return _clamp(p_of_z(ih, z)), _clamp(p_of_z(ia, z))


_METHODS = {
    "multiplicative": _multiplicative,
    "additive": _additive,
    "shin": _shin,
}


def devig(home_price: float, away_price: float, method: str = "multiplicative") -> DevigFair:
    if method not in _METHODS:
        raise ValueError(f"unknown devig method {method!r}; choose from {sorted(_METHODS)}")
    ih = american_to_prob(home_price)
    ia = american_to_prob(away_price)
    overround = ih + ia - 1.0
    ph, pa = _METHODS[method](ih, ia)
    return DevigFair(home=ph, away=pa, overround=overround, method=method)
