// =============================================================================
// Atlas Stream Processing — deploy all acme-billing processors
// Run: mongosh "$ASP_URI" --file infra/asp/deploy_all.js
// =============================================================================
// Each processor lives in its own file under infra/asp/processors/. This
// file just loads them in deterministic order. To deploy a single processor
// in isolation:
//
//   mongosh "$ASP_URI" --file infra/asp/processors/02-rule-discount-mismatch.js
//
// Each processor file is self-contained: it sources _common.js for shared
// constants/helpers and calls deployProcessor() at the bottom.
// =============================================================================

load("infra/asp/processors/00-event-ingest.js");
load("infra/asp/processors/01-feature-rolling-writer.js");
load("infra/asp/processors/01b-feature-window-5m.js");
load("infra/asp/processors/02-rule-discount-mismatch.js");
load("infra/asp/processors/03-rule-velocity-anomaly.js");
load("infra/asp/processors/04-rule-entitlement-mismatch.js");
load("infra/asp/processors/05-rule-geographic-anomaly.js");
load("infra/asp/processors/06-rule-duplicate-transaction.js");
// Phase B.2 — Acme-named rules
load("infra/asp/processors/07-rule-termination-fee-check.js");
load("infra/asp/processors/08-rule-unearned-earned-segregation.js");
load("infra/asp/processors/09-rule-double-charge-multi-code.js");
load("infra/asp/processors/10-rule-proration-check.js");

// =============================================================================
// Final state
// =============================================================================
print("\n=== Listing acme-* processors ===");
const all = sp.listStreamProcessors();
const acme = all.filter(p => p.name.startsWith("acme-"));
for (const p of acme) {
  print(`  ${p.name}: ${p.state} ${p.errorMsg ? '(ERROR: ' + p.errorMsg + ')' : ''}`);
}
print("\nDone.");
