"""bayesian_posterior — Normal-Normal conjugate treatment-effect posterior."""

from __future__ import annotations

import math

import pytest

from research_assistant.tools.data_science.bayesian import bayesian_posterior


def test_flat_prior_matches_frequentist_tail() -> None:
    """With a flat prior the posterior is N(estimate, se^2), so the tail
    probability equals the one-sided frequentist result. log-HR -0.3, se
    0.15, threshold 0 → P(effect<0) = Phi(2.0) ≈ 0.9772."""
    r = bayesian_posterior(estimate=-0.30, se=0.15, threshold=0.0)
    assert r["posterior_mean"] == pytest.approx(-0.30)
    assert r["posterior_sd"] == pytest.approx(0.15)
    assert r["prob_less_than_threshold"] == pytest.approx(0.9772, abs=1e-3)
    assert r["prob_greater_than_threshold"] == pytest.approx(0.0228, abs=1e-3)


def test_credible_interval_is_estimate_plus_minus_1_96_se() -> None:
    r = bayesian_posterior(estimate=0.0, se=1.0, cred_level=0.95)
    lo, hi = r["credible_interval"]
    assert lo == pytest.approx(-1.96, abs=0.01)
    assert hi == pytest.approx(1.96, abs=0.01)


def test_probabilities_sum_to_one() -> None:
    r = bayesian_posterior(estimate=0.4, se=0.2, threshold=0.1)
    assert r["prob_less_than_threshold"] + r["prob_greater_than_threshold"] == pytest.approx(1.0)


def test_informative_prior_shrinks_toward_prior_mean() -> None:
    """A prior centred at 0 pulls a positive estimate back toward 0, and
    tightens the posterior SD below the likelihood SE."""
    flat = bayesian_posterior(estimate=0.5, se=0.2)
    shrunk = bayesian_posterior(estimate=0.5, se=0.2, prior_mean=0.0, prior_sd=0.2)
    assert 0.0 < shrunk["posterior_mean"] < flat["posterior_mean"]
    assert shrunk["posterior_sd"] < flat["posterior_sd"]
    # Equal precision prior + likelihood → posterior mean is the midpoint.
    assert shrunk["posterior_mean"] == pytest.approx(0.25, abs=1e-6)


def test_exponentiate_reports_ratio_scale() -> None:
    """log-HR -0.3 → HR exp(-0.3) ≈ 0.741, with the CI exponentiated too."""
    r = bayesian_posterior(estimate=-0.30, se=0.15, exponentiate=True)
    assert r["exp_posterior_mean"] == pytest.approx(math.exp(-0.30), abs=1e-4)
    lo, hi = r["exp_credible_interval"]
    assert lo == pytest.approx(math.exp(-0.30 - 1.96 * 0.15), abs=1e-3)
    assert hi == pytest.approx(math.exp(-0.30 + 1.96 * 0.15), abs=1e-3)


def test_higher_credible_level_widens_interval() -> None:
    narrow = bayesian_posterior(estimate=0.0, se=1.0, cred_level=0.80)
    wide = bayesian_posterior(estimate=0.0, se=1.0, cred_level=0.99)
    assert wide["credible_interval"][1] > narrow["credible_interval"][1]


def test_flat_prior_is_reported() -> None:
    r = bayesian_posterior(estimate=0.1, se=0.1)
    assert r["prior"]["type"] == "flat"


def test_rejects_non_positive_se() -> None:
    with pytest.raises(ValueError, match="se must be positive"):
        bayesian_posterior(estimate=0.0, se=0.0)


def test_rejects_bad_cred_level() -> None:
    with pytest.raises(ValueError, match="cred_level"):
        bayesian_posterior(estimate=0.0, se=1.0, cred_level=1.5)


def test_rejects_non_positive_prior_sd() -> None:
    with pytest.raises(ValueError, match="prior_sd must be positive"):
        bayesian_posterior(estimate=0.0, se=1.0, prior_sd=0.0)
