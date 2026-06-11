"""Generic JSON-string argument coercion — models sometimes pass object/array
args as JSON strings (e.g. chart_vegalite spec='{"mark": "bar"}'); the executor
parses them back to native objects before the handler's type check."""

from __future__ import annotations

import json

from agent_driver.tools.executor.governed import _coerce_json_string_args


def test_stringified_object_is_parsed():
    out = _coerce_json_string_args({"spec": json.dumps({"mark": "bar"}), "n": 5})
    assert out["spec"] == {"mark": "bar"}
    assert out["n"] == 5  # non-string untouched


def test_stringified_array_is_parsed():
    out = _coerce_json_string_args({"values": "[1, 2, 3]"})
    assert out["values"] == [1, 2, 3]


def test_json_fenced_object_is_parsed():
    out = _coerce_json_string_args({"spec": '```json\n{"mark": "line"}\n```'})
    assert out["spec"] == {"mark": "line"}


def test_plain_string_left_untouched():
    out = _coerce_json_string_args({"name": "hello", "path": "/tmp/x"})
    assert out == {"name": "hello", "path": "/tmp/x"}


def test_non_json_braces_string_left_untouched():
    # Looks like it starts with { but isn't valid JSON → leave as the original str.
    out = _coerce_json_string_args({"q": "{not json"})
    assert out["q"] == "{not json"


def test_number_like_string_left_untouched():
    # Only object/array literals are coerced; scalars stay strings.
    out = _coerce_json_string_args({"x": "42", "y": "true"})
    assert out["x"] == "42" and out["y"] == "true"
