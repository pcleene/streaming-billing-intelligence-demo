"""PR-8 — Embedding text template tests.

Pure-function tests over the static helpers on `EmbeddingService`.
After PR-A (ADR-032) `EmbeddingService` is a static-method namespace —
there is no instance state and no SDK call. The remaining helpers
`case_to_embedding_text` and `history_to_embedding_text` build the
deterministic strings that are written to `embed_source.text` for
Atlas AutoEmbed to vectorise in-cluster.

Verifies that the rich case template surfaces:
  - rules_triggered names + evidence summaries
  - transaction_summary fields (total_myr, charge_codes, anomaly hint)
  - customer_snapshot (customer_type / segment, lifetime quarantine count)
  - severity, priority_band, disposition, analyst notes

…and that the new `history_to_embedding_text` covers the corpus shape.
"""

from __future__ import annotations

from app.services.embedding_service import EmbeddingService


def _rich_case() -> dict:
    return {
        "case_id": "case-rich-1",
        "severity": "high",
        "priority_band": "P1",
        "disposition": "true_positive",
        "analyst_notes": "Promo expired 3 days before billing.",
        "rules_triggered": [
            {
                "rule_type": "discount_mismatch",
                "rule_name": "Discount mismatch",
                "evidence": {
                    "expected_discount": 5.00,
                    "applied_discount": 8.50,
                    "promo_id": "PR-EXP-2026Q1",
                },
            },
            {
                "rule_type": "package_charge_drift",
                "rule_name": "Package charge drift",
                "evidence": {"package_code": "ACME_HD"},
            },
        ],
        "transaction_summary": {
            "transaction_id": "txn-1",
            "transaction_type": "monthly_billing",
            "channel": "auto_debit",
            "subtotal_myr": 100.0,
            "total_discount_myr": 60.0,  # > 50% → anomaly
            "tax_myr": 4.0,
            "total_myr": 44.0,
            "items_count": 2,
            "items_summary": [
                {"name": "Acme HD", "charge_code": "CC_HD",
                 "unit_price_myr": 80.0, "line_total_myr": 80.0},
                {"name": "Acme Sports", "charge_code": "CC_SPORT",
                 "unit_price_myr": 20.0, "line_total_myr": 20.0},
            ],
            "discounts_applied": [
                {"promo_id": "PR-EXP-2026Q1", "type": "percentage",
                 "amount_myr": 50.0},
                {"promo_id": "PR-LOYALTY", "type": "fixed",
                 "amount_myr": 10.0},
            ],
        },
        "customer_snapshot": {
            "customer_id": "cust-1",
            "name": "Acme Sdn Bhd",
            "segment": "commercial",
            "tier": "Gold",
            "lifetime_quarantine_count": 4,
        },
    }


def test_case_to_embedding_text_surfaces_rich_signals() -> None:
    text = EmbeddingService.case_to_embedding_text(_rich_case())

    # Severity + priority appear together.
    assert "Severity high" in text
    assert "P1" in text

    # Both rules + at least one piece of evidence each are present.
    assert "discount_mismatch" in text
    assert "package_charge_drift" in text
    assert "expected_discount=5.0" in text or "expected_discount=5" in text
    assert "promo_id=PR-EXP-2026Q1" in text

    # Transaction context: total + channel + charge codes + anomaly hint.
    assert "44.00 MYR" in text
    assert "auto_debit" in text
    assert "CC_HD" in text and "CC_SPORT" in text
    assert "anomaly:high_discount_ratio" in text

    # Per-line bundle shape: charge_code @unit=line_total chunks land
    # in the embedding text so similar bundles cluster in vector space.
    assert "lines:" in text
    assert "@80.00=80.00" in text
    assert "@20.00=20.00" in text

    # Per-discount promo shape: promo_id:type=amount chunks ground
    # the embedding in the discount structure (not just total).
    assert "discounts:" in text
    assert "PR-EXP-2026Q1:percentage=50.00" in text
    assert "PR-LOYALTY:fixed=10.00" in text

    # Customer snapshot summary.
    assert "commercial" in text
    assert "lifetime_quarantine=4" in text
    assert "Gold" in text

    # Disposition + analyst notes.
    assert "true_positive" in text
    assert "Promo expired" in text


def test_case_to_embedding_text_handles_lean_legacy_shape() -> None:
    """The legacy PR-1 shape (no transaction_summary / priority_band) must
    still produce a non-empty deterministic string so the existing
    indexing path keeps working."""
    lean = {
        "case_id": "legacy-1",
        "severity": "low",
        "rules_triggered": [{"rule_type": "discount_mismatch", "rule_name": "DM"}],
        "amount": 12.5,
        "customer_snapshot": {"segment": "residential"},
        "analyst_notes": "",
    }
    text = EmbeddingService.case_to_embedding_text(lean)
    assert "residential" in text
    assert "Severity low" in text
    assert "discount_mismatch" in text
    # Falls back to the amount path when no transaction_summary present.
    assert "12.50 MYR" in text


def test_history_to_embedding_text_covers_corpus_fields() -> None:
    history = {
        "case_id": "hist-1",
        "customer_segment": "residential",
        "severity": "medium",
        "disposition": "false_positive",
        "analyst_notes": "Customer had legitimate promo.",
        "resolution_summary": "Closed without action.",
        "rules_triggered": [
            {
                "rule_type": "discount_mismatch",
                "rule_name": "Discount mismatch",
                "evidence": {"reason": "valid_promo"},
            }
        ],
        "transaction_summary": {
            "transaction_id": "txn-h-1",
            "transaction_type": "one_off",
            "channel": "pos",
            "subtotal_myr": 50.0,
            "total_discount_myr": 5.0,
            "total_myr": 45.0,
            "items_summary": [
                {"name": "Acme Box", "charge_code": "CC_BOX",
                 "line_total_myr": 45.0},
            ],
        },
        "learnings": {
            "pattern_name": "valid_promo_misread",
            "root_cause": "Rule did not honour promo extension.",
        },
    }
    text = EmbeddingService.history_to_embedding_text(history)

    assert "Resolved quarantine case" in text
    assert "residential" in text
    assert "Severity medium" in text
    assert "false_positive" in text
    assert "discount_mismatch" in text
    assert "reason=valid_promo" in text
    assert "45.00 MYR" in text
    assert "CC_BOX" in text
    assert "Closed without action" in text
    assert "Customer had legitimate promo" in text
    assert "valid_promo_misread" in text
    assert "Rule did not honour promo extension" in text


# ---------------------------------------------------------------------
# PR-9 customer_to_embedding_text tests were removed in PR-A (ADR-032).
# Atlas AutoEmbed owns the customer vector path now — there is no
# application-side helper to test. See
# `docs/2026-05-08-legacy-byo-voyage-embedding-archive.md`.
# ---------------------------------------------------------------------
