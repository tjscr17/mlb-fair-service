"""De-vig correctness tests."""

import math

import pytest

from mlb_fair.odds.devig import american_to_prob, devig


def test_american_to_prob():
    assert american_to_prob(-110) == pytest.approx(0.523809, abs=1e-5)
    assert american_to_prob(+130) == pytest.approx(0.434783, abs=1e-5)
    assert american_to_prob(-150) == pytest.approx(0.6, abs=1e-9)
    assert american_to_prob(+100) == pytest.approx(0.5, abs=1e-9)


def test_methods_sum_to_one():
    for method in ("multiplicative", "additive", "shin"):
        f = devig(-150, +130, method=method)
        assert f.home + f.away == pytest.approx(1.0, abs=1e-6)
        assert 0 < f.home < 1 and 0 < f.away < 1


def test_pickem_is_fifty_fifty():
    f = devig(-110, -110, method="multiplicative")
    assert f.home == pytest.approx(0.5, abs=1e-9)
    assert f.away == pytest.approx(0.5, abs=1e-9)


def test_overround_reported():
    f = devig(-110, -110)
    # two -110s => implied 0.5238 each => ~4.76% hold
    assert f.overround == pytest.approx(0.047619, abs=1e-5)


def test_favorite_bias_ordering():
    # Multiplicative removes margin *proportionally*, taking a bigger absolute bite
    # out of the higher-implied (favorite) side, so it shades the favorite DOWN
    # relative to additive/Shin.
    fav_price, dog_price = -250, +210
    mult = devig(fav_price, dog_price, "multiplicative")
    add = devig(fav_price, dog_price, "additive")
    shin = devig(fav_price, dog_price, "shin")
    # all agree the favorite (home) is the favorite
    assert mult.home > 0.5 and add.home > 0.5 and shin.home > 0.5
    # multiplicative gives the favorite the lowest fair of the three
    assert mult.home < add.home
    # shin sits between multiplicative and additive (inclusive; ~equals additive at low vig)
    assert mult.home <= shin.home <= add.home + 1e-9


def test_heavy_favorite_no_negative():
    f = devig(-2000, +1200, "additive")
    assert 0 < f.away < 1  # additive clamp kept the longshot positive
    assert f.home + f.away == pytest.approx(1.0, abs=1e-6)
