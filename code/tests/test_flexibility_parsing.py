"""
Unit tests for ingest/loaders.py::_coerce_flexibility — the real dataset uses
a string-valued `flexibility` column ("flexible" / "protected") rather than
a plain boolean, which _coerce_bool alone would silently turn into False for
every row (since none of those strings match true/false/1/0/yes/no).

Run with:
    python3 -m pytest code/tests/test_flexibility_parsing.py -v
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ingest.loaders import _coerce_flexibility


def test_string_valued_flexibility_column():
    s = pd.Series(["flexible", "protected", "flexible", "protected"])
    assert list(_coerce_flexibility(s)) == [True, False, True, False]


def test_boolean_style_column_still_works():
    s = pd.Series(["true", "false", "1", "0", "yes", "no"])
    assert list(_coerce_flexibility(s)) == [True, False, True, False, True, False]


def test_synonyms_are_recognized():
    s = pd.Series(["discretionary", "essential", "optional", "fixed", "mandatory"])
    assert list(_coerce_flexibility(s)) == [True, False, True, False, False]


def test_unrecognized_and_missing_values_default_to_protected():
    s = pd.Series(["something_unexpected", None, float("nan")])
    assert list(_coerce_flexibility(s)) == [False, False, False]


def test_case_and_whitespace_insensitive():
    s = pd.Series([" Flexible ", "PROTECTED"])
    assert list(_coerce_flexibility(s)) == [True, False]
