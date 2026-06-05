"""Embedding text builders (AutoEmbed era, ADR-032).

Pre-PR-A this module wrapped the Voyage SDK so application code could
produce 1024-dim vectors directly. ADR-032 retired that wrapper: Atlas
Auto Embedding now reads `embed_source.text` and produces the vector
in-cluster. The class still exists as a *namespace* for the canonical
text builders (`case_to_embedding_text`, `history_to_embedding_text`)
because they are imported from many call sites and run as pure helpers
— no DB, no network. See
`docs/2026-05-08-legacy-byo-voyage-embedding-archive.md` for the
pre-AutoEmbed wrapper if a revert is ever needed.

Templates MUST stay deterministic. Changing a template invalidates the
existing AutoEmbed corpus on the next index rebuild.
"""

from __future__ import annotations


class EmbeddingService:
    """Pure text-builder namespace.

    All methods are `@staticmethod` so callers can use either
    `EmbeddingService.case_to_embedding_text(case)` or instantiate with
    `EmbeddingService()` — both are free of side effects.
    """

    # ------------------------------------------------------------------
    # Embedding text templates
    # ------------------------------------------------------------------
    #
    # Used by both the indexing path (`scripts/seed_history.py` /
    # `QuarantineService._archive_to_history`) and the query path
    # (`RagService.assist`).
    #
    # The templates are defensive about missing / partial inputs so the
    # same helper works on the lean PR-1 case shape and the rich PR-7+
    # `QuarantineCaseV3` shape.

    @staticmethod
    def _format_amount(value) -> str:
        try:
            return f"{float(value):.2f}"
        except (TypeError, ValueError):
            return "0.00"

    @staticmethod
    def _summarise_rules(rules_triggered: list) -> str:
        """Render rules as 'rule_type:rule_name (evidence: k=v, ...)' chunks."""
        if not rules_triggered:
            return "(none)"
        chunks: list[str] = []
        for r in rules_triggered:
            if not isinstance(r, dict):
                continue
            rtype = r.get("rule_type") or "?"
            rname = r.get("rule_name") or ""
            evidence = r.get("evidence") or {}
            if isinstance(evidence, dict) and evidence:
                # Keep evidence summary short — top 4 keys, str-coerced.
                ev_pairs = list(evidence.items())[:4]
                ev_str = ", ".join(f"{k}={v}" for k, v in ev_pairs)
                chunks.append(f"{rtype}:{rname} (evidence: {ev_str})".strip())
            else:
                chunks.append(f"{rtype}:{rname}".strip())
        return "; ".join(chunks) if chunks else "(none)"

    @staticmethod
    def _summarise_lines(items: list) -> str:
        """Render line items as 'charge_code xN @unit=line' chunks.

        Surfaces structural shape of the bundle (which products at
        which prices) so AutoEmbed can match on bundle similarity, not
        just total amount. Capped at the first 8 lines so a pathological
        invoice doesn't blow the embedding budget — Voyage-4 handles
        ~32k tokens but the shorter the deterministic template, the
        more weight each token carries in the cosine space.
        """
        if not items:
            return ""
        chunks: list[str] = []
        for i in items[:8]:
            if not isinstance(i, dict):
                continue
            cc = i.get("charge_code") or "?"
            qty = i.get("quantity")
            unit = i.get("unit_price_myr")
            line_total = i.get("line_total_myr")
            qty_str = f"x{int(qty)} " if qty not in (None, "", 1) else ""
            unit_str = (
                f"@{EmbeddingService._format_amount(unit)}"
                if unit is not None
                else ""
            )
            line_str = (
                f"={EmbeddingService._format_amount(line_total)}"
                if line_total is not None
                else ""
            )
            chunks.append(f"{cc} {qty_str}{unit_str}{line_str}".strip())
        return "; ".join(chunks)

    @staticmethod
    def _summarise_discounts(discounts: list) -> str:
        """Render per-discount tuples as 'promo_id:type=amount' chunks.

        The discount sub-doc is the strongest predictor of whether a
        case is a true positive (mis-applied promo) or false positive
        (legitimate stacked promos). Surfacing promo_id + type into
        the embedding text means historical cases with the same
        promo structure cluster together in vector space.
        """
        if not discounts:
            return ""
        chunks: list[str] = []
        for d in discounts[:6]:
            if not isinstance(d, dict):
                continue
            pid = d.get("promo_id") or d.get("discount_id") or "?"
            dtype = d.get("type") or d.get("discount_type") or "?"
            amount = d.get("amount_myr") or d.get("amount") or 0
            chunks.append(
                f"{pid}:{dtype}={EmbeddingService._format_amount(amount)}"
            )
        return "; ".join(chunks)

    @staticmethod
    def _summarise_txn(txn_summary: dict | None) -> str:
        """Render the embedded transaction summary in a flat sentence.

        Beyond the legacy total/channel/charge-code roll-up, surfaces:
          - per-line `charge_code xN @unit=line` chunks (bundle shape)
          - per-discount `promo_id:type=amount` chunks (promo shape)

        Both are deterministic and bounded so the AutoEmbed corpus
        stays stable as long as the case-snapshot writers don't
        reorder list items.
        """
        if not txn_summary:
            return ""
        total = EmbeddingService._format_amount(txn_summary.get("total_myr") or 0)
        items = txn_summary.get("items_summary") or []
        charge_codes = sorted({
            i.get("charge_code") for i in items
            if isinstance(i, dict) and i.get("charge_code")
        })
        cc_str = ", ".join(charge_codes) if charge_codes else "n/a"
        chan = txn_summary.get("channel") or "?"
        ttype = txn_summary.get("transaction_type") or "?"
        # Simple anomaly hint: discount > 50% of subtotal
        subtotal = float(txn_summary.get("subtotal_myr") or 0)
        discount = float(txn_summary.get("total_discount_myr") or 0)
        anomaly = ""
        if subtotal > 0 and discount / subtotal > 0.5:
            anomaly = " [anomaly:high_discount_ratio]"
        line_text = EmbeddingService._summarise_lines(items)
        # Discount sub-docs may live under any of these keys depending on
        # which writer produced the snapshot — V3 calls it
        # `discounts_applied`, the case archiver flattens it as
        # `discount_breakdown`.
        discounts = (
            txn_summary.get("discounts_applied")
            or txn_summary.get("discount_breakdown")
            or []
        )
        discount_text = EmbeddingService._summarise_discounts(discounts)
        parts = [
            f"Transaction {ttype} via {chan}, total {total} MYR, "
            f"charge_codes [{cc_str}]{anomaly}"
        ]
        if line_text:
            parts.append(f"lines: {line_text}")
        if discount_text:
            parts.append(f"discounts: {discount_text}")
        return ". ".join(parts)

    @staticmethod
    def _summarise_customer(snap: dict | None) -> str:
        if not snap:
            return ""
        ctype = snap.get("customer_type") or "unknown"
        lifetime = int(snap.get("lifetime_quarantine_count") or 0)
        tier = snap.get("tier") or ""
        tail = f" tier={tier}" if tier else ""
        return f"Customer {ctype} (lifetime_quarantine={lifetime}){tail}"

    @staticmethod
    def case_to_embedding_text(case: dict) -> str:
        """Canonical text for a quarantine case used for both indexing & queries.

        Keep this stable — changing it invalidates the existing corpus.

        The template is intentionally verbose: rules + their evidence
        summaries, transaction context (total, channel, charge codes,
        anomaly hint), customer snapshot (type, lifetime quarantine
        count, tier), severity & priority band — all concatenated
        into one flat string. Voyage-4 (Atlas-side) handles the length
        comfortably.
        """
        snap = case.get("customer_snapshot") or {}
        ctype = snap.get("customer_type") or "unknown"
        tier = snap.get("tier") or "unknown"
        rules_text = EmbeddingService._summarise_rules(
            case.get("rules_triggered") or []
        )
        txn_text = EmbeddingService._summarise_txn(case.get("transaction_summary"))
        cust_text = EmbeddingService._summarise_customer(snap)

        amt = (
            (case.get("transaction_summary") or {}).get("total_myr")
            or 0
        )
        severity = case.get("severity", "unknown")
        priority_band = case.get("priority_band") or "n/a"
        disposition = case.get("disposition") or "unresolved"
        notes = case.get("analyst_notes") or ""

        parts = [
            f"Quarantine case for {ctype} {tier}-tier customer.",
            f"Severity {severity}, priority {priority_band}.",
            f"Rules triggered: {rules_text}.",
        ]
        if txn_text:
            parts.append(txn_text + ".")
        else:
            parts.append(f"Amount {EmbeddingService._format_amount(amt)} MYR.")
        if cust_text:
            parts.append(cust_text + ".")
        parts.append(f"Disposition {disposition}.")
        if notes:
            parts.append(f"Analyst notes: {notes}")
        return " ".join(p for p in parts if p).strip()

    @staticmethod
    def history_to_embedding_text(history: dict) -> str:
        """Canonical text for a *historical* (resolved) V3 case.

        Used by the history-corpus seed and by
        `QuarantineService._archive_to_history` to populate
        `embed_source.text`. Combines the final disposition, analyst
        notes, resolution summary, rules triggered, and transaction
        summary into one flat string suitable for AutoEmbed.
        """
        rules_text = EmbeddingService._summarise_rules(
            history.get("rules_triggered") or []
        )
        txn_text = EmbeddingService._summarise_txn(history.get("transaction_summary"))
        ctype = history.get("customer_type") or "unknown"
        tier = (
            history.get("customer_tier")
            or (history.get("customer_context_summary") or {}).get("tier")
            or "unknown"
        )
        severity = history.get("severity", "unknown")
        disposition = history.get("disposition") or "unknown"
        notes = history.get("analyst_notes") or ""
        resolution_summary = history.get("resolution_summary") or ""
        learnings = history.get("learnings") or {}
        learning_chunks: list[str] = []
        if isinstance(learnings, dict):
            if learnings.get("pattern_name"):
                learning_chunks.append(f"pattern: {learnings['pattern_name']}")
            if learnings.get("root_cause"):
                learning_chunks.append(f"root_cause: {learnings['root_cause']}")
        learning_text = "; ".join(learning_chunks)

        parts = [
            f"Resolved quarantine case for {ctype} {tier}-tier customer.",
            f"Severity {severity}.",
            f"Final disposition: {disposition}.",
            f"Rules triggered: {rules_text}.",
        ]
        if txn_text:
            parts.append(txn_text + ".")
        if resolution_summary:
            parts.append(f"Resolution: {resolution_summary}")
        if notes:
            parts.append(f"Analyst notes: {notes}")
        if learning_text:
            parts.append(f"Learnings: {learning_text}.")
        return " ".join(p for p in parts if p).strip()
