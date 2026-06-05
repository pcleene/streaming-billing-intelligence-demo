# Atlas setup — indexes, Atlas Search, Vector Search

All commands assume `mongosh` connected to the Streaming Billing cluster:

```bash
mongosh "$MONGODB_URL" --tls --tlsCertificateKeyFile "$TLS_CERT_PATH" \
  --eval 'use streaming_billing'
```

The seed script (`make seed`) creates these indexes automatically on first run.
This document is the source of truth and a recovery script.

---

## 1. Standard indexes

```javascript
use streaming_billing

// customers
db.customers.createIndex({ customer_id: 1 }, { unique: true });
db.customers.createIndex({ "address.state": 1 });
db.customers.createIndex({ segment: 1, "subscriptions.package_code": 1 });

// transactions
db.transactions.createIndex({ transaction_id: 1 }, { unique: true });
db.transactions.createIndex({ customer_id: 1, timestamp: -1 });
db.transactions.createIndex({ timestamp: -1 });
db.transactions.createIndex({ merchant_id: 1, timestamp: -1 });

// quarantine_rules
db.quarantine_rules.createIndex({ name: 1 }, { unique: true });
db.quarantine_rules.createIndex({ enabled: 1, mode: 1 });
db.quarantine_rules.createIndex({ rule_type: 1 });

// quarantine_cases
db.quarantine_cases.createIndex({ created_at: -1 });
db.quarantine_cases.createIndex({ customer_id: 1, created_at: -1 });
db.quarantine_cases.createIndex({ status: 1, severity: 1 });
db.quarantine_cases.createIndex({ "rules_triggered.rule_id": 1 });

// quarantine_cases_history (RAG corpus)
db.quarantine_cases_history.createIndex({ disposition: 1 });
db.quarantine_cases_history.createIndex({ "rules_triggered.rule_type": 1 });
db.quarantine_cases_history.createIndex({ resolved_at: -1 });

// features
db.features.createIndex({ customer_id: 1 }, { unique: true });
db.features.createIndex({ updated_at: -1 });

// crm_snapshots
db.crm_snapshots.createIndex({ as_of: -1 });
```

---

## 2. Schema validation (selected collections)

`quarantine_rules` uses `$jsonSchema` to enforce the discriminated rule_type.
This is a defense-in-depth boundary; primary validation is at the Pydantic
layer.

```javascript
db.runCommand({
  collMod: "quarantine_rules",
  validator: {
    $jsonSchema: {
      bsonType: "object",
      required: ["name", "rule_type", "severity", "enabled", "mode", "version"],
      properties: {
        name:        { bsonType: "string", minLength: 3 },
        rule_type:   { enum: [
          "discount_mismatch", "velocity_anomaly", "amount_outlier",
          "entitlement_mismatch", "geographic_anomaly", "duplicate_transaction"
        ]},
        severity:    { enum: ["low", "medium", "high", "critical"] },
        enabled:     { bsonType: "bool" },
        mode:        { enum: ["active", "shadow"] },
        version:     { bsonType: "int", minimum: 1 },
        parameters:  { bsonType: "object" },
        metrics:     { bsonType: "object" }
      }
    }
  },
  validationLevel: "moderate",
  validationAction: "warn"
});
```

---

## 3. Atlas Search index — `customers`

For autocomplete/fuzzy search on customer name + IC + account id, no `$regex`.

Index name: `customers_search_idx`

```json
{
  "mappings": {
    "dynamic": false,
    "fields": {
      "name": [
        { "type": "autocomplete", "tokenization": "edgeGram", "minGrams": 2, "maxGrams": 15 },
        { "type": "string", "analyzer": "lucene.standard" }
      ],
      "ic_number": { "type": "string", "analyzer": "lucene.keyword" },
      "account_id": { "type": "string", "analyzer": "lucene.keyword" },
      "email":      { "type": "string", "analyzer": "lucene.keyword" },
      "segment":    { "type": "token" },
      "address.state": { "type": "token" }
    }
  }
}
```

Create via Atlas UI (Search → Create Search Index → JSON editor) on
`streaming_billing.customers`, or via the Admin API.

---

## 4. Atlas Vector Search index — `quarantine_cases_history`

For RAG retrieval. Voyage `voyage-4-large` embeddings, 1024 dims, cosine.

Index name: `case_history_vector_idx`

```json
{
  "fields": [
    {
      "type": "vector",
      "path": "embedding",
      "numDimensions": 1024,
      "similarity": "cosine"
    },
    { "type": "filter", "path": "rules_triggered.rule_type" },
    { "type": "filter", "path": "disposition" },
    { "type": "filter", "path": "customer_segment" },
    { "type": "filter", "path": "severity" }
  ]
}
```

Create via Atlas UI on `streaming_billing.quarantine_cases_history`. The filter
fields enable the metadata-filtered vector search central to ADR-004.

---

## 5. Verifying index health

```javascript
db.customers.getIndexes();
db.quarantine_rules.getIndexes();
db.quarantine_cases_history.getSearchIndexes();   // includes vector indexes
```

Expect:

- `customers`: `_id_`, `customer_id_1`, `address.state_1`, `segment_1_subscriptions.package_code_1`
- `customers_search_idx` (Atlas Search): `READY`
- `quarantine_cases_history`: standard indexes + `case_history_vector_idx` `READY`
