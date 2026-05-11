"""Shared test fixtures."""

import numpy as np
import pandas as pd
import pytest


@pytest.fixture
def monotone_series() -> pd.Series:
    """Ascending series 1..5 with datetime index."""
    return pd.Series(
        [1.0, 2.0, 3.0, 4.0, 5.0],
        index=pd.date_range("2020-01-01", periods=5, freq="MS"),
    )


@pytest.fixture
def flat_series() -> pd.Series:
    """All-same values."""
    return pd.Series(
        [5.0, 5.0, 5.0],
        index=pd.date_range("2020-01-01", periods=3, freq="MS"),
    )


@pytest.fixture
def series_with_nans() -> pd.Series:
    return pd.Series(
        [1.0, np.nan, 3.0, np.nan, 5.0],
        index=pd.date_range("2020-01-01", periods=5, freq="MS"),
    )


@pytest.fixture
def cpi_series() -> pd.Series:
    """CPI YoY values: 5%, 8%, 2% — last one is at target."""
    return pd.Series(
        [5.0, 8.0, 2.0],
        index=pd.date_range("2022-01-01", periods=3, freq="MS"),
    )
