"""Unit coverage for the N-run field-variance reporter."""

from tools.repeatability_validate import build_report, compare_runs


def test_compare_runs_reports_exact_field_variance():
    runs = [
        [
            {
                "customer_name": "Edgen Murray",
                "items": [{"quantity": 4, "length_ft": 10.0}],
            }
        ],
        [
            {
                "customer_name": "Edgen Murray",
                "items": [{"quantity": 4, "length_ft": 12.0}],
            }
        ],
        [
            {
                "customer_name": "Edgen Murray",
                "items": [{"quantity": 4, "length_ft": 12.0}],
            }
        ],
    ]

    result = compare_runs(runs)

    assert result["stable"] is False
    assert result["variant_field_count"] == 1
    assert result["variant_fields"] == [
        {
            "field": "quotes[0].items[0].length_ft",
            "variants": [
                {"value": 10.0, "runs": [1]},
                {"value": 12.0, "runs": [2, 3]},
            ],
        }
    ]


def test_compare_runs_reports_quote_and_item_count_variance():
    result = compare_runs(
        [
            [{"items": [{"quantity": 1}]}],
            [{"items": [{"quantity": 1}, {"quantity": 2}]}],
        ]
    )

    fields = {field["field"] for field in result["variant_fields"]}
    assert "quotes[0].items.__count__" in fields
    assert "quotes[0].items[1].quantity" in fields


def test_build_report_counts_errors_as_variant():
    reports = [
        {"stable": True, "variant_field_count": 0},
        {"stable": False, "variant_field_count": 2},
    ]

    result = build_report(reports, "claude", 5)

    assert result["stable_email_count"] == 1
    assert result["variant_email_count"] == 1
    assert result["variant_field_count"] == 2
