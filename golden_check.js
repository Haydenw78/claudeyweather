/* Prove forecast-core.js behaves identically everywhere it is actually run:
   loaded via Node's require() (the source of truth below), loaded via
   <script src> inside index.html (the shipped app), loaded via <script src>
   inside calibration/index.html (the calibration harness), and reached
   through the Python bridge (forecast_client.py -> node_runner.js).

   Earlier versions of this file hand-transcribed the ceiling formula as a
   literal string and compared forecast-core.js against that transcription.
   That worked only because nothing else in the codebase reimplemented the
   formula independently once index.html was refactored to just call
   ForecastCore.predict() - which means the transcription was comparing
   forecast-core.js against a hand-typed copy of itself. It also could not
   see calibration/index.html or the Python path at all: the one comparison
   it ran (index.html vs forecast-core.js) was the one pair that was never
   going to drift, and the two pairs that DID drift (calibration/index.html's
   own dispersion solver, and calibration/engine.py's reimplemented ceiling)
   were both invisible to it. Now there is nothing to hand-transcribe:
   every path is checked by calling the SAME ForecastCore.predict against
   the SAME inputs and diffing the outputs. */
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');
const { JSDOM } = require('jsdom');
const core = require('./forecast-core.js');

/* ---- constant key-set guard -------------------------------------------
   A guard that only checks the keys it already knows about is the same
   failure mode as the hand-transcribed oracle, one layer up: silently
   passing while a new (or removed) term goes completely untested. So this
   does not just read WEIGHTS/BED - it asserts their key sets match exactly
   what this file has been written to exercise, and refuses to run at all
   if they do not. */
const EXPECTED_WEIGHT_KEYS = ['base', 'wStir', 'wWind', 'wEkman', 'wSst', 'rainPerMm', 'rainCap',
  'windOn', 'windDen', 'windExp', 'rainDecayKm', 'oceanicBase', 'oceanicStir', 'oceanicWind',
  'estTideFloor', 'estPlume', 'metresExp'];
const EXPECTED_BED_TYPES = ['oceanic', 'shelf', 'estuarine'];
const EXPECTED_BED_FIELDS = ['uCrit', 'supply'];

function assertExactKeys(actual, expected, label) {
  const a = Object.keys(actual).sort(), e = [...expected].sort();
  const missing = e.filter(k => !a.includes(k));
  const extra = a.filter(k => !e.includes(k));
  if (missing.length || extra.length) {
    console.log(`FATAL: ${label} key mismatch - refusing to run.`);
    if (extra.length) console.log(`  new key(s) in forecast-core.js, not covered by this harness: ${extra.join(', ')}`);
    if (missing.length) console.log(`  key(s) this harness expects but forecast-core.js no longer has: ${missing.join(', ')}`);
    console.log('  A constant was added to or removed from WEIGHTS/BED without golden_check.js being');
    console.log('  updated to match. Update the EXPECTED_* lists at the top of this file - and check');
    console.log('  whether the new or removed term needs its own test coverage below - before trusting');
    console.log('  this harness again.');
    process.exit(1);
  }
}
assertExactKeys(core.WEIGHTS, EXPECTED_WEIGHT_KEYS, 'WEIGHTS');
assertExactKeys(core.BED, EXPECTED_BED_TYPES, 'BED');
for (const type of EXPECTED_BED_TYPES) assertExactKeys(core.BED[type], EXPECTED_BED_FIELDS, `BED.${type}`);
console.log(`WEIGHTS and BED key sets match what this harness expects (${EXPECTED_WEIGHT_KEYS.length} weights, ${EXPECTED_BED_TYPES.length} bed types).`);

/* ---- load both HTML pages in jsdom ------------------------------------- */
function loadPage(htmlPath) {
  const dom = new JSDOM(fs.readFileSync(htmlPath, 'utf8'),
    { runScripts: 'dangerously', pretendToBeVisual: true, resources: 'usable',
      url: 'file://' + path.resolve(htmlPath) });
  return dom;
}
const domApp = loadPage('index.html');
const domCal = loadPage('calibration/index.html');

setTimeout(() => {
  const evApp = s => domApp.window.eval(s);
  const evCal = s => domCal.window.eval(s);
  for (const [label, ev] of [['index.html', evApp], ['calibration/index.html', evCal]]) {
    if (ev("typeof ForecastCore") === 'undefined') {
      console.log(`FATAL: forecast-core.js did not load inside ${label}.`);
      console.log(`  ${label} expects <script src="forecast-core.js"> beside it.`);
      process.exit(1);
    }
  }

  let n = 0, bad = 0, worst = 0, worstCase = null;
  const byPath = { 'index.html': { n: 0, bad: 0 }, 'calibration/index.html': { n: 0, bad: 0 }, 'python': { n: 0, bad: 0 } };
  /* A missing value must FAIL, not pass. The first version of this used
     `Math.abs(a-b) > 1e-12`, and NaN > 1e-12 is false, so every comparison
     against an undefined field counted as agreement. That hid a real field-name
     mismatch through two clean runs. Compare types first, then values. */
  const rec = (a, b, label, c, pathLabel) => {
    n++; if (pathLabel) byPath[pathLabel].n++;
    const where = () => label + ' ' + JSON.stringify(c) + (pathLabel ? ' [' + pathLabel + ']' : '');
    if (typeof a !== 'number' || typeof b !== 'number' || !Number.isFinite(a) || !Number.isFinite(b)) {
      bad++; if (pathLabel) byPath[pathLabel].bad++;
      if (!worstCase) worstCase = 'NON-NUMERIC ' + where() + ' got ' + a + ' vs ' + b;
      return;
    }
    const d = Math.abs(a - b);
    if (d > 1e-12) { bad++; if (pathLabel) byPath[pathLabel].bad++; }
    if (d > worst) { worst = d; worstCase = where(); }
  };

  // orbitalVel across the supported domain - same file, Node require() vs jsdom script-load
  for (const H of [0, 0.5, 1.2, 2, 3]) for (const T of [4, 8, 12, 18]) for (const d of [5, 10, 20, 35, 200])
    rec(evApp(`orbitalVel(${H},${T},${d})`), core.orbitalVel(H, T, d), 'orbitalVel', { H, T, d }, 'index.html');

  // windMix: same check (ForecastCore.windMix - index.html does not alias it to a bare global)
  for (const g of [0, 8, 11, 11.0001, 15, 20, 25, 34, 45, 60, 120])
    rec(evApp(`ForecastCore.windMix(${g})`), core.windMix(g), 'windMix', { g }, 'index.html');

  // bedStir across type/shelter/depth/trains
  for (const type of ['oceanic', 'shelf', 'estuarine'])
    for (const shel of [1, 0.7, 0.25]) for (const d of [5, 10, 25])
      for (const trains of [[{ h: 1, p: 8 }], [{ h: 2, p: 12 }, { h: 0.6, p: 5 }], []]) {
        const js = evApp(`(function(){const tr=${JSON.stringify(trains)};
          const e=tr.reduce((a,t)=>a+Math.pow(orbitalVel(t.h*${shel},t.p,${d}),2),0);
          const ub=Math.sqrt(e); const bed=BED['${type}']||BED.shelf;
          return [ub, Math.min(3,bed.supply*ub/bed.uCrit)];})()`);
        const me = core.bedStir(trains, type, shel, d);
        rec(js[0], me.ub, 'bedStir.ub', { type, shel, d }, 'index.html');
        rec(js[1], me.stir, 'bedStir.stir', { type, shel, d }, 'index.html');
      }

  /* ---- predict(): the real end-to-end check --------------------------
     No hand-typed formula. Each path is asked to run ITS OWN loaded copy of
     forecast-core.js against the same (features, spot) and the results are
     diffed against core.predict() (Node's require()'d copy). A structural
     change to the formula, a weight, a branch condition or the metres curve
     shows up here automatically - there is nothing in this file to keep in
     sync by hand. */
  const spots = [{ type: 'shelf', offshoreKm: 5.8, vMin: 3, vMax: 25 },
                 { type: 'shelf', offshoreKm: 1.8, vMin: 2, vMax: 18 },
                 { type: 'oceanic', offshoreKm: 80, vMin: 10, vMax: 35 },
                 { type: 'estuarine', offshoreKm: 0.3, vMin: 1, vMax: 6 },
                 { type: 'shelf' }, { type: 'oceanic' }, { type: 'estuarine' }];

  const cases = [];
  for (const spot of spots)
    for (const stirLag of [0, 0.5, 1.6, 3])
      for (const mixKmh of [0, 11, 20, 34, 60])
        for (const ekman of [-0.5, 0, 0.4])
          for (const sstAnom of [-3, 0, 1.5, 3])
            for (const rain72 of [0, 10, 60])
              for (const tideQ of [0, 0.06, 0.5, 1])
                for (const plume of [0, 0.5, 1])
                  cases.push({ features: { stirLag, mixKmh, ekman, sstAnom, rain72, season: 0, tideQ, plume }, spot });

  console.log(`running ${cases.length} predict() cases against index.html and calibration/index.html...`);
  for (const { features: f, spot } of cases) {
    const nodeResult = core.predict(f, spot);
    for (const [pathLabel, ev] of [['index.html', evApp], ['calibration/index.html', evCal]]) {
      const r = ev(`ForecastCore.predict(${JSON.stringify(f)}, ${JSON.stringify(spot)})`);
      rec(r.offshoreCeiling, nodeResult.offshoreCeiling, 'ceiling', { spot: spot.type, stirLag: f.stirLag, mixKmh: f.mixKmh }, pathLabel);
      rec(r.visibilityIndex, nodeResult.visibilityIndex, 'vis', { spot: spot.type, stirLag: f.stirLag, mixKmh: f.mixKmh, tideQ: f.tideQ, plume: f.plume }, pathLabel);
      rec(r.visibilityMetres, nodeResult.visibilityMetres, 'visM', { spot: spot.type, stirLag: f.stirLag, mixKmh: f.mixKmh }, pathLabel);
      byPath[pathLabel].n++;
      if (r.visibilityBand !== nodeResult.visibilityBand) {
        bad++; byPath[pathLabel].bad++; n++;
        worstCase = `visBand [${pathLabel}] ` + JSON.stringify({ spot: spot.type, vis: r.visibilityIndex });
      } else n++;
    }
  }

  /* ---- Python path: same cases, through forecast_client.py's real bridge */
  console.log(`running ${cases.length} predict() cases through the Python bridge (forecast_client.py -> node_runner.js)...`);
  let pyResults = null;
  try {
    const out = execFileSync('python3', ['golden_check_python.py'], {
      input: JSON.stringify(cases), maxBuffer: 1024 * 1024 * 256, encoding: 'utf8'
    });
    pyResults = JSON.parse(out);
  } catch (e) {
    console.log('FATAL: Python bridge check failed to run.');
    console.log('  ' + String(e && e.message || e));
    process.exit(1);
  }
  if (pyResults.length !== cases.length) {
    console.log(`FATAL: Python bridge returned ${pyResults.length} results for ${cases.length} cases.`);
    process.exit(1);
  }
  for (let i = 0; i < cases.length; i++) {
    const nodeResult = core.predict(cases[i].features, cases[i].spot);
    const r = pyResults[i];
    const c = { spot: cases[i].spot.type, stirLag: cases[i].features.stirLag, mixKmh: cases[i].features.mixKmh };
    rec(r.offshoreCeiling, nodeResult.offshoreCeiling, 'ceiling', c, 'python');
    rec(r.visibilityIndex, nodeResult.visibilityIndex, 'vis', c, 'python');
    rec(r.visibilityMetres, nodeResult.visibilityMetres, 'visM', c, 'python');
    byPath.python.n++;
    if (r.visibilityBand !== nodeResult.visibilityBand) {
      bad++; byPath.python.bad++; n++;
      worstCase = 'visBand [python] ' + JSON.stringify(c);
    } else n++;
  }

  console.log(`\ncomparisons: ${n}`);
  console.log(`disagreements beyond 1e-12: ${bad}`);
  console.log(`worst absolute difference: ${worst.toExponential(3)}`);
  if (worstCase) console.log(`worst case: ${worstCase}`);
  console.log('\nby path:');
  for (const [label, r] of Object.entries(byPath)) {
    console.log(`  ${label.padEnd(24)} ${r.n} compared, ${r.bad} disagreements`);
  }
  console.log(bad === 0 ? '\nEQUIVALENT' : '\nNOT EQUIVALENT');
  process.exit(bad === 0 ? 0 : 1);
}, 2500);
