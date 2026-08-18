#!/usr/bin/env node
/* node_runner.js
 *
 * Thin stdin/stdout bridge over forecast-core.js. One long-lived process,
 * JSON lines in, JSON lines out, so Python can push tens of thousands of rows
 * through folds and permutation nulls without paying process-spawn cost per
 * row. Spawning node per call would be slow enough to make a 2,000-draw null
 * impractical, which is how nulls quietly get shrunk.
 *
 * Protocol, one JSON object per input line:
 *   { "id": <any>, "features": {...}, "spot": {...}, "weights": {...}? }
 *   { "id": <any>, "cmd": "constants" }
 * One JSON object per output line, same id echoed:
 *   { "id": <any>, "ok": true, "result": {...component trace...} }
 *   { "id": <any>, "ok": true, "result": {"WEIGHTS": {...}, "BED": {...}} }  (cmd:"constants")
 *   { "id": <any>, "ok": false, "error": "..." }
 *
 * "constants" exists so a Python caller can build a full weights dict to
 * override (predict() replaces WEIGHTS wholesale, it does not merge a
 * partial override onto it) without hand-copying the numbers - that copy is
 * exactly the drift this bridge exists to prevent.
 *
 * Output order matches input order. Errors on one row do not kill the process.
 */

'use strict';
const readline = require('readline');
const core = require('./forecast-core.js');

const rl = readline.createInterface({ input: process.stdin, terminal: false });

rl.on('line', line => {
  const s = line.trim();
  if (!s) return;
  let req;
  try {
    req = JSON.parse(s);
  } catch (e) {
    process.stdout.write(JSON.stringify({ id: null, ok: false, error: 'bad json: ' + e.message }) + '\n');
    return;
  }
  try {
    if (req.cmd === 'constants') {
      process.stdout.write(JSON.stringify({ id: req.id, ok: true, result: { WEIGHTS: core.WEIGHTS, BED: core.BED } }) + '\n');
      return;
    }
    const result = core.predict(req.features || {}, req.spot || {}, req.weights);
    process.stdout.write(JSON.stringify({ id: req.id, ok: true, result }) + '\n');
  } catch (e) {
    process.stdout.write(JSON.stringify({ id: req.id, ok: false, error: String(e && e.message || e) }) + '\n');
  }
});

rl.on('close', () => process.exit(0));
