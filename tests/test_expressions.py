import pytest

from owrb.expressions import ExpressionError, evaluate_expression, parse_expression


def test_boolean_rule_over_nested_parameters() -> None:
    context = {
        "traveller": {"requires_wheelchair_access": True},
        "activity": {"physical_level": "high"},
    }
    expression = "not (traveller.requires_wheelchair_access and activity.physical_level == 'high')"
    assert evaluate_expression(expression, context) is False


def test_missing_optional_attribute_resolves_to_none() -> None:
    context = {"traveller": {"party_size": 2}}
    assert evaluate_expression("not traveller.requires_wheelchair_access", context) is True


def test_arithmetic_comparison_and_functions() -> None:
    context = {"max_distance_km": 50, "location": {"minimum_practical_radius_km": 25}}
    assert evaluate_expression(
        "max_distance_km >= location.minimum_practical_radius_km", context
    )
    assert evaluate_expression("min(max_distance_km, 30) + 5", context) == 35
    assert evaluate_expression("max_distance_km / 4", context) == 12.5


def test_membership_and_conditional() -> None:
    context = {"season": "summer"}
    assert evaluate_expression("season in ['summer', 'autumn']", context)
    assert evaluate_expression("10 if season == 'summer' else 20", context) == 10


def test_unknown_name_is_rejected() -> None:
    with pytest.raises(ExpressionError, match="unknown name"):
        evaluate_expression("undefined_parameter > 1", {})


@pytest.mark.parametrize(
    "expression",
    [
        "__import__('os')",
        "().__class__",
        "traveller.__class__",
        "[x for x in values]",
        "lambda: 1",
        "open('/etc/passwd')",
        "traveller.description.upper()",
        "2 ** 100",
    ],
)
def test_unsafe_expressions_are_rejected(expression: str) -> None:
    with pytest.raises(ExpressionError):
        parse_expression(expression)


def test_syntax_error_is_reported() -> None:
    with pytest.raises(ExpressionError, match="syntax"):
        parse_expression("1 +")
