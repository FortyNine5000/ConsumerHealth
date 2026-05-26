import sys
import types

import pandas as pd


class _NoopLogger:
    def __getattr__(self, name):
        return lambda *args, **kwargs: None


sys.modules.setdefault(
    "structlog",
    types.SimpleNamespace(get_logger=lambda *args, **kwargs: _NoopLogger()),
)

from ingestion.sources.nyfed_hhdc import _parse_hhdc_sheet, _quarter_start


def test_quarter_start_parses_hhdc_header_variants():
    assert _quarter_start("2003:Q1") == "2003-01-01"
    assert _quarter_start("03:Q2") == "2003-04-01"
    assert _quarter_start("Q3 2003") == "2003-07-01"
    assert _quarter_start("2003Q4") == "2003-10-01"


def test_parse_hhdc_sheet_extracts_credit_card_and_student_loan_rows():
    df = pd.DataFrame([
        ["Loan Type", "03:Q1", "2003:Q2", "Q3 2003", "2003Q4"],
        ["Mortgage", 1.0, 1.1, 1.2, 1.3],
        ["Credit Card", 7.0, 7.1, 7.2, 7.3],
        ["Student Loan", 8.0, 8.1, 8.2, 8.3],
        ["Auto Loan", 2.0, 2.1, 2.2, 2.3],
    ])

    parsed = _parse_hhdc_sheet(df)

    assert parsed["NYFED_HHDC_CC_SERIOUS_DELINQ"] == [
        {"date": "2003-01-01", "value": 7.0},
        {"date": "2003-04-01", "value": 7.1},
        {"date": "2003-07-01", "value": 7.2},
        {"date": "2003-10-01", "value": 7.3},
    ]
    assert parsed["NYFED_HHDC_STUDENT_LOAN_SERIOUS_DELINQ"] == [
        {"date": "2003-01-01", "value": 8.0},
        {"date": "2003-04-01", "value": 8.1},
        {"date": "2003-07-01", "value": 8.2},
        {"date": "2003-10-01", "value": 8.3},
    ]
