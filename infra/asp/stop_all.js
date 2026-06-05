// Stop & drop all acme-* ASP processors.
// Run: mongosh "$ASP_URI" --file infra/asp/stop_all.js

const all = sp.listStreamProcessors();
const acme = all.filter(p => p.name.startsWith("acme-"));
for (const p of acme) {
  print(`Stopping ${p.name} (state=${p.state}) ...`);
  try { sp[p.name].stop(); } catch (e) { print(`  stop failed: ${e.message}`); }
  try { sp[p.name].drop(); print(`  dropped`); } catch (e) { print(`  drop failed: ${e.message}`); }
}
print("Done.");
