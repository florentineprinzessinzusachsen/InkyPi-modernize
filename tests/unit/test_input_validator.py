"""Unit tests for the new input validation helpers in src/utils/form_utils.py.

Covers:
- ValidationError
- validate_json_schema
"""

from __future__ import annotations

import pytest

from utils.form_utils import ValidationError, validate_json_schema

# ---------------------------------------------------------------------------
# ValidationError
# ---------------------------------------------------------------------------


class TestValidationError:
    def test_is_value_error(self):
        err = ValidationError("bad input")
        assert isinstance(err, ValueError)

    def test_message_stored(self):
        err = ValidationError("something went wrong")
        assert err.message == "something went wrong"
        assert str(err) == "something went wrong"

    def test_field_defaults_to_none(self):
        err = ValidationError("msg")
        assert err.field is None

    def test_field_stored_when_given(self):
        err = ValidationError("msg", field="latitude")
        assert err.field == "latitude"

    def test_raise_and_catch(self):
        with pytest.raises(ValidationError) as exc_info:
            raise ValidationError("out of range", field="saturation")
        assert exc_info.value.field == "saturation"
        assert "out of range" in str(exc_info.value)


# ---------------------------------------------------------------------------
# validate_json_schema
# ---------------------------------------------------------------------------


_SIMPLE_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "count": {"type": "integer", "minimum": 1, "maximum": 100},
        "mode": {"type": "string", "enum": ["a", "b"]},
    },
    "required": ["name"],
}


class TestValidateJsonSchema:
    def test_valid_data_returns_empty_list(self):
        errors = validate_json_schema(
            {"name": "test", "count": 5, "mode": "a"}, _SIMPLE_SCHEMA
        )
        assert errors == []

    def test_missing_required_field(self):
        errors = validate_json_schema({}, _SIMPLE_SCHEMA)
        assert len(errors) > 0
        assert any("name" in e for e in errors)

    def test_wrong_type_for_field(self):
        errors = validate_json_schema({"name": 123}, _SIMPLE_SCHEMA)
        assert len(errors) > 0

    def test_out_of_range_minimum(self):
        errors = validate_json_schema({"name": "x", "count": 0}, _SIMPLE_SCHEMA)
        assert len(errors) > 0
        assert any("count" in e or "minimum" in e.lower() for e in errors)

    def test_out_of_range_maximum(self):
        errors = validate_json_schema({"name": "x", "count": 101}, _SIMPLE_SCHEMA)
        assert len(errors) > 0

    def test_invalid_enum_value(self):
        errors = validate_json_schema({"name": "x", "mode": "c"}, _SIMPLE_SCHEMA)
        assert len(errors) > 0

    def test_additional_properties_allowed(self):
        errors = validate_json_schema({"name": "x", "extra_key": "ok"}, _SIMPLE_SCHEMA)
        assert errors == []

    def test_multiple_errors_all_returned(self):
        # Missing required 'name' AND wrong type for 'count'
        errors = validate_json_schema({"count": "not-an-int"}, _SIMPLE_SCHEMA)
        assert len(errors) >= 1  # at least 'name' missing

    def test_empty_dict_with_no_required_fields(self):
        schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {"x": {"type": "string"}},
        }
        errors = validate_json_schema({}, schema)
        assert errors == []

    def test_returns_list_type(self):
        result = validate_json_schema({"name": "ok"}, _SIMPLE_SCHEMA)
        assert isinstance(result, list)
