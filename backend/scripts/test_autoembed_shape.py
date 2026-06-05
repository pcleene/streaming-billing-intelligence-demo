"""Probe what AutoEmbed actually accepts as a path.

Run this to settle the question: does Atlas Vector Search AutoEmbed
require a leaf string field, or can it index an object whose sub-fields
are each strings?

Usage (from repo root):
    .venv/bin/python -m scripts.test_autoembed_shape

Reads MONGODB_URL + TLS_CERT_PATH from .env (same as the rest of the app).
Uses a throwaway database `acme_autoembed_probe` so it cannot disturb
real data.

What it tries (in order):

  1. Path = `embed_source.text`             (leaf string under a parent)
  2. Path = `embed_source`                  (object with string sub-fields)
  3. Path = `embed_source.case_summary`     (leaf string at depth 1, alt name)
  4. Index with TWO autoEmbed entries: `embed_source.case_summary`
     AND `embed_source.rules_text`           (multi-field index)

For each, it:
  - Creates the index via createSearchIndex on the test collection.
  - Polls `listSearchIndexes` until status is READY or FAILED (timeout 5m).
  - Reports the outcome verbatim (queryable=True, status, any latestDefinition
    error message).
  - For the ones that succeed, runs a $vectorSearch with query.text and
    reports the top hit's score.
  - Drops the index.

Cleanup: drops the test collection at the end, regardless of success.

Prereq: the Voyage API key is configured at the Atlas project level
(Project Settings -> Vector Search Embedding Models -> Voyage AI).
If it isn't, every index creation will fail with a clear "credential not
found" error and this script will print that error verbatim — which is
itself useful information.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from pymongo import AsyncMongoClient

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(REPO_ROOT / ".env")

MONGODB_URL   = os.environ["MONGODB_URL"]
TLS_CERT_PATH = os.environ["TLS_CERT_PATH"]

PROBE_DB   = "acme_autoembed_probe"
PROBE_COLL = "probe"

# Voyage model that should be present in the Atlas project credentials.
EMBED_MODEL = "voyage-4-large"


# --------------------------------------------------------------------------
# Sample documents — same shape variants we want to validate
# --------------------------------------------------------------------------

DOCS = [
    {
        "_id": "doc_concat",
        "label": "single concatenated text under parent",
        "embed_source": {
            "text": (
                "Resolved quarantine case for residential platinum-tier "
                "customer in Selangor. Acme Family Pack HD subscription, "
                "Hari Raya RM30 rebate missing on April bill. Disposition "
                "false_positive — CRM-to-billing batch lag. Auto-resolved "
                "after next sync; no compensation."
            ),
        },
    },
    {
        "_id": "doc_object",
        "label": "object with multiple string sub-fields, no .text leaf",
        "embed_source": {
            "case_summary":     "Resolved quarantine case for residential platinum-tier customer in Selangor.",
            "rules_text":       "discount_mismatch: Hari Raya RM30 rebate missing on bill (PROMO_RAYA_2026 valid 2026-04-01..2026-06-30).",
            "transaction_text": "subscription_charge via auto_debit, total 169.55 MYR, charge_codes [CC_SUB_MTHLY].",
            "customer_text":    "Customer residential platinum tier; tenure 21 months; lifetime_quarantine=4; service_state=Selangor.",
            "resolution_text":  "Rebate appeared on next CRM sync 11h after billing run; auto-resolved.",
            "learnings_text":   "Pattern crm_lag_during_promo_rollout. Real-time CRM-to-billing path would have prevented the false positive.",
        },
    },
]


# --------------------------------------------------------------------------
# Index definitions to try
# --------------------------------------------------------------------------

def autoembed_field(path: str) -> dict:
    return {
        "type": "autoEmbed",
        "modality": "text",
        "path": path,
        "model": EMBED_MODEL,
        "numDimensions": 1024,
        "similarity": "dotProduct",
        "quantization": "scalar",
    }


INDEX_TRIALS = [
    {
        "name": "trial_object_path",
        "definition": {"fields": [autoembed_field("embed_source")]},
        "purpose": "parent OBJECT as the path — does Atlas accept this?",
    },
]


# --------------------------------------------------------------------------
# Probe
# --------------------------------------------------------------------------

async def wait_for_status(coll, idx_name: str, *, timeout_s: int = 300) -> dict:
    """Poll listSearchIndexes until status is terminal (READY, FAILED, ...)."""
    start = time.time()
    last: dict = {}
    while time.time() - start < timeout_s:
        async for spec in await coll.list_search_indexes(name=idx_name):
            last = spec
            status = (spec.get("status") or "").upper()
            if status in {"READY", "FAILED", "DOES_NOT_EXIST"}:
                return spec
        await asyncio.sleep(3)
    return last or {"status": "TIMEOUT"}


async def try_query_text(coll, idx_name: str, path: str) -> dict | None:
    """Run a $vectorSearch with query.text against the given index/path."""
    try:
        cursor = await coll.aggregate([
            {"$vectorSearch": {
                "index": idx_name,
                "path": path,
                "query": {"text": "Hari Raya rebate missing on bill"},
                "numCandidates": 50,
                "limit": 5,
            }},
            {"$set": {"score": {"$meta": "vectorSearchScore"}}},
            {"$project": {"_id": 1, "label": 1, "score": 1}},
        ])
        hits = []
        async for doc in cursor:
            hits.append(doc)
        return {"ok": True, "hits": hits}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


async def main() -> None:
    print(f"Connecting to {MONGODB_URL.split('@')[-1].split('?')[0]} ...")
    client = AsyncMongoClient(
        MONGODB_URL,
        tls=True,
        tlsCertificateKeyFile=TLS_CERT_PATH,
    )
    db = client[PROBE_DB]
    coll = db[PROBE_COLL]

    # Fresh start
    await coll.drop()
    await coll.insert_many(DOCS)
    print(f"Inserted {len(DOCS)} probe documents into {PROBE_DB}.{PROBE_COLL}\n")

    results: list[dict] = []
    for trial in INDEX_TRIALS:
        idx_name = trial["name"]
        purpose  = trial["purpose"]
        print("=" * 78)
        print(f"TRIAL: {purpose}")
        print(f"  index: {idx_name}")
        print(f"  definition: {trial['definition']}")

        # Best-effort cleanup if a prior run left it behind.
        try:
            await coll.drop_search_index(idx_name)
            await asyncio.sleep(1)
        except Exception:
            pass

        try:
            await coll.create_search_index(model={
                "name":       idx_name,
                "type":       "vectorSearch",
                "definition": trial["definition"],
            })
            print(f"  createSearchIndex: accepted, polling for READY ...")
        except Exception as exc:  # noqa: BLE001
            results.append({
                "trial":    idx_name,
                "purpose":  purpose,
                "create":   "REJECTED",
                "error":    str(exc),
            })
            print(f"  createSearchIndex: REJECTED -> {exc}")
            continue

        spec = await wait_for_status(coll, idx_name)
        status = (spec.get("status") or "UNKNOWN").upper()
        latest_err = (spec.get("latestDefinition") or {}).get("status") or {}
        msg = spec.get("statusDetail") or spec.get("error") or latest_err
        print(f"  status: {status}")
        if status != "READY":
            print(f"  detail: {msg}")
            results.append({
                "trial":   idx_name,
                "purpose": purpose,
                "create":  "ACCEPTED",
                "status":  status,
                "detail":  str(msg),
            })
            try:
                await coll.drop_search_index(idx_name)
            except Exception:
                pass
            continue

        # Index is READY — try a real query
        # Pick a sensible search path: for trial 4 (multi-field) we query
        # the first one; for trial 2 (object) we query the parent path.
        if idx_name == "trial_multi_autoembed":
            search_path = "embed_source.case_summary"
        elif idx_name == "trial_object_path":
            search_path = "embed_source"
        elif idx_name == "trial_leaf_case_summary":
            search_path = "embed_source.case_summary"
        else:
            search_path = "embed_source.text"

        query = await try_query_text(coll, idx_name, search_path)
        results.append({
            "trial":   idx_name,
            "purpose": purpose,
            "create":  "ACCEPTED",
            "status":  status,
            "query":   query,
        })
        print(f"  $vectorSearch on path={search_path!r}: {query}")

        try:
            await coll.drop_search_index(idx_name)
        except Exception:
            pass

    # Cleanup
    await coll.drop()

    print("\n" + "=" * 78)
    print("VERDICT")
    print("=" * 78)
    r = results[0] if results else {}
    create = r.get("create")
    status = r.get("status")
    query  = r.get("query") or {}

    if create == "REJECTED":
        print("FAIL — Atlas refused to create the index with path = object.")
        print(f"  Atlas error: {r.get('error')}")
        print("  Conclusion: AutoEmbed requires a leaf string path.")
        print("  The prompt is RIGHT — keep embed_source.text.")
    elif create == "ACCEPTED" and status != "READY":
        print(f"FAIL — index creation accepted but status = {status}.")
        print(f"  Detail: {r.get('detail')}")
        print("  Conclusion: AutoEmbed cannot embed an object path (rejected at sync).")
        print("  The prompt is RIGHT — keep embed_source.text.")
    elif create == "ACCEPTED" and status == "READY" and query.get("ok"):
        hits = query.get("hits") or []
        print(f"PASS — index reached READY and $vectorSearch returned {len(hits)} hits.")
        for h in hits:
            print(f"  {h.get('_id')}: score={h.get('score'):.4f}  ({h.get('label')})")
        print("  Conclusion: AutoEmbed accepts an object path.")
        print("  The prompt is WRONG — Paul is right; reshape to put fields under the parent.")
    else:
        print(f"INCONCLUSIVE — create={create}, status={status}, query_ok={query.get('ok')}")
        print(f"  Raw result: {r}")
    print()


if __name__ == "__main__":
    asyncio.run(main())
