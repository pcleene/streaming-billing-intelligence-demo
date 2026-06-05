# Demo script — 20-minute walkthrough

> Audience: Acme Malaysia analytics team. Sophisticated, skeptical of vendor
> claims. Goal: leave with the conviction that this is a coherent platform,
> not 4 stitched demos.

## 0. Setup (before audience arrives)

```bash
make install          # one-time
make seed             # populates 10k customers + 500 historical cases
make asp-deploy       # pushes ASP processors
make demo             # docker-compose up
```

Open <http://localhost:5173>. Confirm tiles are populating on the Overview
page (transactions/sec ticking up).

---

## 1. The current pain (90 seconds)

> "Today your analytics team gets transactions in batches. Customer context
> lives in Redshift, refreshed every few hours. So when a transaction arrives
> with a 'discount applied', you can't tell if the promotion is real, expired,
> or simply not yet in Redshift. Watch what happens with that gap."

Open **Customer 360** → search a customer (e.g. "Aisyah" or "Tan Wei"). Toggle
**CRM lag = 4 hours**. Show the alert: "Customer's profile is stale — last
refreshed 4h ago. 3 promotions may be missing."

Toggle off. Show the unified view updates instantly: "MongoDB consolidates the
CRM, billing, and entitlements into a single live document — no Redshift
batch wait."

---

## 2. Real-time quarantine + Rule Studio (5 minutes — *the headline*)

Switch to **Live Operations** tab. Point at the live tile:
"100 transactions/second sustained, P99 < 200 ms end-to-end."

Then switch to **Rule Studio**.

> "Today, when a fraud analyst wants a new quarantine rule, they file a ticket.
> Engineering writes Redshift SQL, deploys, waits for the next batch. Watch."

Click **+ New Rule** → pick `discount_mismatch` from the card grid. Walk
through the dynamic form:

- Name: "Demo: discount > RM50 with no active promo"
- `discount_field`: `transaction.discount_amount`
- `crm_promotion_field`: `customer.active_promotions`
- `grace_period_minutes`: 60
- Mode: **shadow**
- Severity: **high**

Click **Test against last 1000 transactions** — show the count of cases that
*would* be quarantined.

Save. Show the rule appearing in the list with `mode=shadow`. Switch to **Live
Operations** — the new rule is firing in shadow mode (`hit_count` ticking up,
`status=shadow` so cases are logged but not blocked).

Switch back to Rule Studio → open the rule → click **Promote to Active**.
The rule activation propagates via change stream to ASP. Cases now block.

> "From idea to live rule in under two minutes, by an analyst, with a
> historical replay safety net. No engineering ticket, no batch wait."

---

## 3. AI Analyst Assist (Pillar 3 — 4 minutes)

Switch to **Quarantine Queue**. Click on a high-severity case (e.g. one of the
discount-mismatch cases just generated).

Right pane shows the case detail. Left pane: **AI Analyst Assist**, generating
in real time:

- **Summary**: 2-sentence English description of what's anomalous
- **Likelihood assessment**: `legitimate` / `data_error` / `fraud` with a
  confidence score
- **Cited similar cases**: 3 historical resolved cases with similarity scores,
  each linkable to the resolution notes from the original analyst
- **Recommended next steps**: numbered list

> "The AI didn't make this up. It pulled the top-5 most similar historical
> cases via vector search filtered by rule type, then asked Claude to
> structure a recommendation. Click the citation —"

Click a citation. Show the historical case detail. "This is the institutional
knowledge your senior analysts have been building for years, now retrievable
in milliseconds."

Click **Override** on the AI suggestion, mark as `legitimate`, add a note. The
override is fed back into `quarantine_cases_history` — the next AI suggestion
on a similar case will see this resolution.

---

## 4. Feature store + Databricks integration (Pillar 4 — 4 minutes)

Switch to **Feature Store & Model Lifecycle**.

Show the feature catalog: rolling counts, discount rates, package value vs
spend. Each with a **freshness timestamp** ticking down.

Open the Jupyter notebook (`ml/feature_pipeline_and_training.ipynb`) on a
second screen. Run **Flow 1 — Streaming ingestion**.

> "This is what Databricks does in production. The Spark Connector wraps
> MongoDB Change Streams, and watch — features land in Delta in 4 seconds."

Insert a fresh feature document via the marked cell. Show the Delta count
ticking up.

Switch back to the dashboard. Point at the **streaming lag tile**: ~3 s.

> "Compare to nightly Redshift ETL: a 4-hour blind spot becomes a 3-second
> propagation."

Then run **Flow 2 — Batch training**: IsolationForest fits on the Delta
snapshot, MLflow logs metrics. Mention drift-triggered retraining (KS test on
key features).

> "Streaming feeds keep features current. Training is still batch over a Delta
> snapshot — properly decoupled."

---

## 5. Wrap-up (30 seconds)

Return to Overview. Point at the four tiles:

1. **Single customer view** — CRM lag eliminated.
2. **Real-time quarantine** — 100 txn/sec, rules authored in the UI.
3. **AI assist** — institutional knowledge retrievable.
4. **Feature freshness** — measured in seconds, not hours.

> "One platform, four pain points. Same Atlas cluster, same MSK, same Bedrock
> account you'd plug into Databricks. No rip-and-replace."

---

## Failure-recovery cheatsheet

| Symptom | Likely cause | Recovery |
|---|---|---|
| Overview tiles stuck at 0 | Simulator not running | `make simulator` |
| AI Assist returns empty | Voyage API key missing | check `.env`, restart backend |
| Rule Studio "test" returns 0 | Empty transaction history | `make seed` |
| Streaming lag tile flat | Spark notebook not started | run Flow 1 in `feature_pipeline_and_training.ipynb` |
| ASP processor in `failed` state | Pipeline error | `mongosh "$ASP_URI" --eval "sp['acme-event-ingest'].stats()"` |

---

## What to *not* show

- The MSK console (boring, networking)
- Atlas connection screens (boring, vanity)
- Raw Mongo shell (we are showing platform value, not DB ops)

If asked: "Yes, of course we have observability — but that's not the demo."
