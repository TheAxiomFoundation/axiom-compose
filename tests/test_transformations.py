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


def test_derived_relation_builds_filtered_entity_rule():
    rule = build_transformation(
        "derived_relation",
        {
            "pattern": "derived_relation",
            "name": "snap_unit",
            "effective_from": "2026-01-01",
            "arity": 2,
            "source_relation": "member_of_household",
            "entity": "SnapUnit",
            "member_relation": "members",
            "slot_entities": ["Person", "Household"],
            "predicate": "snap_member_eligible",
            "source": "7 USC 2012(m)",
        },
    )

    assert rule["kind"] == "derived_relation"
    assert rule["derived_relation"]["entity"] == "SnapUnit"
    assert rule["derived_relation"]["source_relation"] == "member_of_household"
    assert rule["derived_relation"]["arity"] == 2
    assert rule["derived_relation"]["slot_entities"] == ["Person", "Household"]
    assert rule["versions"][0]["formula"] == "snap_member_eligible"
    assert rule["source"] == "7 USC 2012(m)"


def test_derived_relation_rejects_missing_source_relation():
    with pytest.raises(TransformationError):
        build_transformation(
            "derived_relation",
            {
                "pattern": "derived_relation",
                "name": "snap_unit",
                "effective_from": "2026-01-01",
                "arity": 2,
                "entity": "SnapUnit",
                "member_relation": "members",
                "slot_entities": ["Person", "Household"],
                "predicate": "snap_member_eligible",
            },
        )


def test_unknown_pattern_fails_loudly():
    with pytest.raises(TransformationError, match="unknown transformation"):
        build_transformation("program_specific", {})
