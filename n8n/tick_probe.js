// Read-only n8n DB accounting probe (SHADOW ops helper; never writes anywhere).
//
// WHY: N8N_CONTROL_PLANE.md §7.1 — while n8n runs, the HOST must never open the live SQLite DB
// (that would add a second writer on a virtiofs-backed WAL database). This probe runs *inside*
// the container, copies the live trio (database.sqlite / -wal / -shm) to its own diag dir and
// reads the COPY, so tick accounting never touches the live files.
//
// Usage (host):
//   container exec n8n node /host/workspace-ro/quant-runtime-pipeline-n8n/n8n/tick_probe.js
// Output: one JSON document on stdout — integrity_check, last executions, status counts.
// It is deterministic and read-only; it is NOT part of the shadow workflow.
const sqlite3 = require('/usr/local/lib/node_modules/n8n/node_modules/sqlite3');
const fs = require('fs');

const SRC = '/home/node/.n8n';
const D = '/home/node/.n8n/diag/probe';
fs.mkdirSync(D, { recursive: true });
for (const f of ['database.sqlite', 'database.sqlite-wal', 'database.sqlite-shm']) {
  try { fs.copyFileSync(`${SRC}/${f}`, `${D}/${f}`); } catch (e) { if (!/ENOENT/.test(e.message)) throw e; }
}

const all = (db, sql) => new Promise((ok, fail) => db.all(sql, (e, r) => (e ? fail(e) : ok(r))));

const out = { at: new Date().toISOString() };
const db = new sqlite3.Database(`${D}/database.sqlite`, async (err) => {
  if (err) { console.log(JSON.stringify({ ...out, open_error: err.message })); return; }
  try {
    const ic = await all(db, 'PRAGMA integrity_check');
    out.integrity_check = Object.values(ic[0])[0];
    out.last_executions = await all(db, 'SELECT id, status, mode, startedAt, stoppedAt FROM execution_entity ORDER BY id DESC LIMIT 6');
    out.status_counts = await all(db, 'SELECT status, count(*) AS n FROM execution_entity GROUP BY status');
    const mx = await all(db, 'SELECT max(id) AS max_id, count(*) AS total FROM execution_entity');
    out.max_id = mx[0].max_id; out.total = mx[0].total;
  } catch (e) {
    out.error = `${e.code || ''} ${e.message}`;
  }
  db.close(() => console.log(JSON.stringify(out, null, 2)));
});
