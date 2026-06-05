# Atlas Auto Embedding — Operator Runbook

This runbook covers the **one-time per-environment** Atlas-side
configuration that lets MongoDB Atlas Vector Search call Voyage AI on
our behalf. The application code never sees the Voyage API key. See
ADR-032 and `docs/2026-05-04-autoembed-migration-and-seed-realism.md`
for context.

> **Do not paste the API key into this repo.** Not into `.env`, not
> into `.env.example`, not into `docker-compose.yml`, not into
> `pyproject.toml`, not into `app/config.py`. The key lives only in
> Atlas project settings.

## 1. Configure the Voyage credential in Atlas

Per environment (dev, demo, prod):

1. Atlas UI → **Project Settings** → **Vector Search Embedding
   Models** (or **Atlas Search → Embedding Models**, depending on the
   cluster generation).
2. Click **Add credential** → provider **Voyage AI** → paste the API
   key → **Save**.
3. Verify the credential by listing available models from the same
   panel; `voyage-4-large` should appear with **1024 dimensions**.

## 2. Smoke-test the credential before creating indexes

Before running `python -m scripts.setup_indexes` with
`FF_AUTOEMBED=true`, confirm Atlas can call Voyage. The cheapest test
is to create a one-off AutoEmbed index on a throwaway collection and
watch it transition to `status: READY`. If Atlas reports
`credential not found` or `embedding provider error`, the credential
was not saved correctly — return to step 1.

## 3. Run the application setup

```bash
FF_AUTOEMBED=true python -m scripts.setup_indexes
```

The script:

- Drops the legacy manual-vector index (`case_history_vector_idx`) if
  present.
- Creates the AutoEmbed index `case_history_autoembed_idx` on
  `quarantine_cases_history` (the RAG corpus). Customer collections
  are not vector-indexed — they were retired in V3.
- Polls the index every 2 seconds (timeout 600s) until
  `status: READY`.
- Exits non-zero with a verbatim Atlas error if a credential is
  missing or invalid.

## 4. Index field shape

The AutoEmbed index points at the leaf string `embed_source.text` on
`quarantine_cases_history`. Atlas embeds on insert/update and at index
sync time; the vector is stored invisibly. Filters are declared
inside the index definition. The canonical shape Atlas accepts is:

```json
{
  "name": "case_history_autoembed_idx",
  "type": "vectorSearch",
  "definition": {
    "fields": [
      {
        "type": "autoEmbed",
        "path": "embed_source.text",
        "modality": "text",
        "model": "voyage-4-large"
      },
      { "type": "filter", "path": "rules_triggered.rule_type" },
      { "type": "filter", "path": "disposition" },
      { "type": "filter", "path": "customer_tier" },
      { "type": "filter", "path": "severity" }
    ]
  }
}
```

Note: the field `type` is **`autoEmbed`** (not `vector`). The legacy
BYO-Voyage shape (`type: vector` + `numDimensions` + `similarity` +
`queryVector` at query time) is no longer in use — see ADR-032 and
`docs/2026-05-08-legacy-byo-voyage-embedding-archive.md`. Adding
`numDimensions`, `similarity`, or `input_type` to an `autoEmbed`
field makes Atlas reject the index with `unrecognized field`.

Queries run via `$vectorSearch` with the natural-language form (the
application never embeds the query text):

```javascript
{ $vectorSearch: {
    index: "case_history_autoembed_idx",
    path:  "embed_source.text",
    query: "<natural-language query string>",
    numCandidates: 100,
    limit: 5
}}
```

## 5. Removing a credential / rolling the key

Replace the Voyage API key in the Atlas UI as in step 1. There is no
application restart required — Atlas picks up the new credential
automatically. Existing indexes continue to serve queries with the
new credential.

## 6. Troubleshooting

| Symptom | Likely cause |
|---|---|
| `setup_indexes` reports `credential not found` | Step 1 was not run for this environment. |
| Index reaches `READY` but `$vectorSearch` returns no results | The indexed path resolves to an object on at least some documents. AutoEmbed silently skips object-typed paths — confirm `embed_source.text` is a string on every document. |
| Index status `FAILED` with `embedding provider error` | Voyage upstream issue or invalid credential — re-add the key and re-create the index. |
| `READY` but recall is poor | `embed_source.text` content is too thin (placeholder strings). Re-run `make seed` after the realism pass (PR-B). |
