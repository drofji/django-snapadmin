"""
tests/test_properties_exporting.py

Property-based tests for export serialization (#QA1d, parts 3 and 4).

The export writers turn row dicts into CSV or newline-delimited JSON, one chunk
at a time, and the crash-safe resume depends on every chunk being a whole
number of records. The laws, for every row ``hypothesis`` can build — Unicode,
embedded newlines and quotes, ``U+2028``, ``Decimal``, datetimes, ``None``,
nested JSON — are:

* **NDJSON** — a chunk of *n* rows is exactly *n* lines, each of which parses
  back to its row (non-JSON types as their ``str()``).
* **CSV** — a chunk of *n* rows is exactly *n* records, each of which parses
  back to its row's text in header order (``None`` as an empty cell).
* **CSV never carries a live formula** — no cell a spreadsheet opens starts
  with ``=``, ``+``, ``-``, ``@``, a tab or a carriage return (CWE-1236, the
  OWASP "CSV injection" list). Found by this file: the XLSX writer already
  pinned such cells to text, the CSV writer wrote them raw.
"""

from __future__ import annotations

import csv
import datetime
import io
import json
import uuid
from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from snapadmin.exporting import CSV_FORMULA_TRIGGERS, _csv_header_bytes, _rows_bytes

#: Text for anything parsed back with ``csv.reader``, which before Python 3.11
#: refuses a NUL byte outright ("line contains NUL"). The CI matrix runs 3.10,
#: so the oracle — not the writer under test — needs NUL kept out.
CSV_TEXT = st.text(alphabet=st.characters(codec="utf-8", exclude_characters="\x00"))

#: Column names that are not themselves formula-shaped (the header's own
#: neutralisation has a law of its own below).
FIELD_NAMES = st.lists(
    CSV_TEXT.filter(lambda s: s[:1] not in "=+-@\t\r").filter(bool),
    min_size=1,
    max_size=5,
    unique=True,
)

SCALARS = st.one_of(
    st.none(),
    CSV_TEXT,
    st.integers(),
    st.floats(allow_nan=False, allow_infinity=False),
    st.booleans(),
    st.decimals(allow_nan=False, allow_infinity=False),
    st.datetimes(),
    st.dates(),
    st.uuids(),
)

#: The same, with any text at all — NDJSON has no NUL restriction to work around.
JSON_SCALARS = st.one_of(SCALARS, st.text())

#: Text that opens with one of the characters a spreadsheet evaluates.
FORMULA_TEXT = st.builds(
    lambda trigger, rest: trigger + rest,
    st.sampled_from(sorted(CSV_FORMULA_TRIGGERS)),
    CSV_TEXT,
)


@st.composite
def batches(draw, values=SCALARS):
    fields = draw(FIELD_NAMES)
    rows = draw(
        st.lists(
            st.fixed_dictionaries({name: values for name in fields}),
            max_size=6,
        )
    )
    return fields, rows


def _as_text(value: object) -> str:
    """What ``str()``-based serialization renders a non-JSON value as."""
    return "" if value is None else str(value)


def _parse_csv(fields: list[str], rows: list[dict]) -> list[list[str]]:
    payload = (_csv_header_bytes(fields) + _rows_bytes(rows, fields, is_csv=True)).decode("utf-8")
    return list(csv.reader(io.StringIO(payload, newline="")))


class TestNdjsonLaws:
    @given(batch=batches(values=JSON_SCALARS))
    def test_n_rows_are_n_lines_that_parse_back(self, batch):
        fields, rows = batch

        text = _rows_bytes(rows, fields, is_csv=False).decode("utf-8")

        lines = text.split("\n")
        assert lines[-1] == ""  # every record, the last included, ends in "\n"
        assert len(lines) - 1 == len(rows)
        for line, row in zip(lines, rows):
            decoded = json.loads(line)
            assert decoded.keys() == row.keys()
            for name, value in row.items():
                if isinstance(value, (Decimal, datetime.date, uuid.UUID)):
                    assert decoded[name] == str(value)
                else:
                    assert decoded[name] == value

    @given(batch=batches(values=FORMULA_TEXT))
    def test_json_keeps_text_verbatim_even_when_it_looks_like_a_formula(self, batch):
        """NDJSON is read by programs, not spreadsheets: neutralising it would
        corrupt the data for the consumers the format exists for."""
        fields, rows = batch

        text = _rows_bytes(rows, fields, is_csv=False).decode("utf-8")

        assert [json.loads(line) for line in text.splitlines() if line] == rows


class TestCsvLaws:
    @given(batch=batches(values=SCALARS.filter(
        lambda v: not (isinstance(v, str) and v[:1] in CSV_FORMULA_TRIGGERS)
    )))
    def test_n_rows_are_n_records_that_parse_back_in_header_order(self, batch):
        fields, rows = batch

        parsed = _parse_csv(fields, rows)

        assert parsed[0] == fields
        assert len(parsed) - 1 == len(rows)
        for record, row in zip(parsed[1:], rows):
            assert record == [_as_text(row[name]) for name in fields]

    @given(batch=batches(values=st.one_of(SCALARS, FORMULA_TEXT)))
    def test_no_text_cell_opens_with_a_formula_trigger(self, batch):
        fields, rows = batch

        parsed = _parse_csv(fields, rows)

        for record, row in zip(parsed[1:], rows):
            for cell, name in zip(record, fields):
                if isinstance(row[name], str):
                    assert cell[:1] not in CSV_FORMULA_TRIGGERS, cell
                else:
                    # A number, date or UUID renders as itself — "-1" included.
                    assert cell == _as_text(row[name])

    @given(batch=batches(values=FORMULA_TEXT))
    def test_a_neutralised_cell_is_the_original_text_behind_one_quote(self, batch):
        fields, rows = batch

        parsed = _parse_csv(fields, rows)

        for record, row in zip(parsed[1:], rows):
            assert record == ["'" + row[name] for name in fields]

    @given(
        fields=st.lists(FORMULA_TEXT, min_size=1, max_size=4, unique=True)
    )
    def test_a_header_cannot_carry_a_formula_either(self, fields):
        """Column names come from a custom export source too, not only from
        ``_meta`` — they reach the same spreadsheet cell as any value."""
        header = next(csv.reader(io.StringIO(_csv_header_bytes(fields).decode("utf-8"), newline="")))

        assert header == ["'" + name for name in fields]

    @given(value=st.one_of(st.integers(max_value=-1), st.decimals(max_value=-1, allow_nan=False)))
    def test_a_negative_number_stays_a_number(self, value):
        """Only *text* is neutralised: ``-5`` from an integer column is data a
        spreadsheet should sum, not a formula, and it must stay unquoted."""
        parsed = _parse_csv(["amount"], [{"amount": value}])

        assert parsed[1] == [str(value)]
