// =============================================================================
// Shared constants + helpers for acme-billing ASP processor scripts.
// Sourced by every processor file via `load("infra/asp/_common.js")`.
// =============================================================================
// Connection registry expected in the ASP workspace (see ADR-001):
//   - UtilitymskKafkaConnection : Kafka source (MSK, IAM auth)
//   - FuelRetail_cluster         : Atlas DB sink (cluster-level, can write to streaming_billing)
// =============================================================================

const KAFKA_CONN = "UtilitymskKafkaConnection";
const ATLAS_CONN = "FuelRetail_cluster";
const DB         = "streaming_billing";
const TOPIC      = "acme-billing-events";

// Stop+drop+create+start a single processor. Idempotent; safe to re-run.
function deployProcessor(proc) {
  print(`\n--- ${proc.name} ---`);
  try { sp[proc.name].stop(); print(`  stopped existing`); } catch (e) { /* not running */ }
  try { sp[proc.name].drop(); print(`  dropped existing`); } catch (e) { /* didn't exist */ }
  try {
    sp.createStreamProcessor(proc.name, proc.pipeline);
    print(`  created`);
  } catch (e) {
    print(`  ERROR creating: ${e.message}`);
    return false;
  }
  try {
    sp[proc.name].start();
    print(`  started`);
    return true;
  } catch (e) {
    print(`  ERROR starting: ${e.message}`);
    return false;
  }
}
