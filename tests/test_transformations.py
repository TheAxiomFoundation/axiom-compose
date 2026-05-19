import pytest

from axiom_compose.transformations import TransformationError, build_transformation


def test_sum_terms_builds_generic_rule():
    rule = build_transformation(
        "sum_terms",
        {
            "pattern": "sum_terms",
            "name": "total",
            "effective_from": "2026-01-01",
            "terms": ["a", "b"],
        },
    )

    assert rule["versions"][0]["formula"] == "a + b"


def test_table_lookup_with_extension_is_parameterized():
    rule = build_transformation(
        "table_lookup_with_extension",
        {
            "pattern": "table_lookup_with_extension",
            "name": "amount",
            "effective_from": "2026-01-01",
            "index": "members",
            "table": "amount_table",
            "extension": "additional_amount",
            "minimum_index": 1,
            "maximum_index": 8,
        },
    )

    assert "amount_table[max(min(members, 8), 1)]" in rule["versions"][0]["formula"]
    assert "additional_amount" in rule["versions"][0]["formula"]


def test_unknown_pattern_fails_loudly():
    with pytest.raises(TransformationError, match="unknown transformation"):
        build_transformation("program_specific", {})
