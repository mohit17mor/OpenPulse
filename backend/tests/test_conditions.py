from openpulse.conditions import evaluate_condition, parse_number


def test_parse_number_handles_currency_and_commas():
    assert parse_number("$1,249.50") == 1249.50


def test_changed_condition_matches_when_values_differ():
    result = evaluate_condition(
        {"type": "changed"},
        previous_value="$129.00",
        current_value="$99.00",
        found=True,
    )

    assert result.matched is True
    assert result.reason == "value_changed"


def test_less_than_condition_compares_extracted_numbers():
    result = evaluate_condition(
        {"type": "less_than", "value": 100},
        previous_value="$129.00",
        current_value="$89.99",
        found=True,
    )

    assert result.matched is True
    assert result.reason == "number_less_than"


def test_disappears_condition_matches_missing_target():
    result = evaluate_condition(
        {"type": "disappears"},
        previous_value="In stock",
        current_value=None,
        found=False,
    )

    assert result.matched is True
    assert result.reason == "target_disappeared"

