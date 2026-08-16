/* forecast-core.js
 *
 * The canonical forecast calculation. Single authority for predictions.
 *
 * Extracted verbatim from index.html (GitHub, commit carrying the Newton
 * dispersion solver). The browser imports this directly; the Python
 * calibration workflow batch-calls it through node_runner.js. Nothing
 * reimplements these formulas anywhere else.
 *
 * BOUNDARY. This module is the engine, not the feature pipeline. It takes
 * prepared features and returns a prediction. It does NOT build stirLag,
 * ekman, sstAnom, rain72, tideQ or plume from hourly history: those are
 * upstream and are tested separately against fixed hourly inputs. Feeding
 * this module raw history would make an equivalence test measure the
 * pipeline instead of the engine.
 *
 * Every function is pure. No DOM, no globals, no dates beyond what is passed.
 */

'use strict';

/* Wrapped so nothing leaks into the global lexical scope. Two <script> tags in
   a browser share one global lexical scope, so a bare top-level `const BED`
   here collides with the app's own declarations and the app script dies with a
   redeclaration error. Only ForecastCore is exposed. */
(function (factory) {
  const API = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = API;
  if (typeof window !== 'undefined') window.ForecastCore = API;
})(function () {

/* ---- constants, verbatim ---- */

const BED = {
  oceanic:   { uCrit: 0.55, supply: 0.30 },  // coral rubble, coarse carbonate
  shelf:     { uCrit: 0.35, supply: 1.00 },  // medium sand, open coast
  estuarine: { uCrit: 0.15, supply: 1.50 }   // fine silt, abundant
};

const VIS_BANDS = [
  { max: 28,  label: 'murky',     col: '#FF4D6D' },
  { max: 50,  label: 'patchy',    col: '#FFA83A' },
  { max: 72,  label: 'good',      col: '#5FC6E8' },
  { max: 101, label: 'excellent', col: '#5FD97A' }
];

/* Weights. Named so calibration can vary them without editing formulas.
   Values are the shipped ones and must not be changed here. */
const WEIGHTS = {
  base: 76, wStir: 34, wWind: 22, wEkman: 10, wSst: 6,
  rainPerMm: 0.9, rainCap: 18,
  windOn: 11, windDen: 34, windExp: 1.15,
  rainDecayKm: 7,
  oceanicBase: 94, oceanicStir: 30, oceanicWind: 10,
  estTideFloor: 0.08, estPlume: 0.45,
  metresExp: 2.5
};

/* Bump when any formula or weight changes. Every exported trace carries it, so
   a fixture can never be silently compared against a different engine. */
const ENGINE_VERSION = 'forecast-core-1.0';

const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

/* ---- wave physics ---- */

/* Orbital velocity at the seabed, linear wave theory.
   Newton on x = kd, where x*tanh(x) = w^2*d/g. The old fixed-point iteration
   was up to 95% wrong in shallow water with long-period swell. */
function orbitalVel(H, T, d) {
  if (!H || !T || T <= 0 || !d || d <= 0) return 0;
  const g = 9.81, w = 2 * Math.PI / T, a = w * w * d / g;
  if (!(a > 0)) return 0;
  let x = a / Math.sqrt(Math.tanh(a));       // Guo initial guess
  for (let k = 0; k < 6; k++) {
    const t = Math.tanh(x), f = x * t - a, fp = t + x * (1 - t * t);
    if (Math.abs(fp) < 1e-15) break;
    const step = f / fp;
    x -= step;
    if (Math.abs(step) < 1e-12) break;
  }
  const sh = Math.sinh(x);
  return sh > 1e-6 ? Math.PI * H / (T * sh) : 0;
}

/* Instantaneous bed stirring from all swell trains combined.
   Sheltered marks see only a fraction of the open-coast swell. */
function bedStir(trains, type, shelter, depth) {
  const shel = shelter != null ? shelter : 1;
  const d = depth || 10;
  const e = (trains || []).reduce(
    (a, t) => a + Math.pow(orbitalVel(t.h * shel, t.p, d), 2), 0);
  const ub = Math.sqrt(e);
  const bed = BED[type] || BED.shelf;
  return { ub, stir: Math.min(3, bed.supply * ub / bed.uCrit), uCrit: bed.uCrit };
}

/* ---- engine terms ---- */

/* Gusts, not sustained wind, do the mixing. Caller supplies whichever the
   app supplies: gust when present, otherwise sustained. */
function windMix(mixKmh, W) {
  const w = W || WEIGHTS;
  return Math.min(1, Math.pow(Math.max(0, (mixKmh || 0) - w.windOn) / w.windDen, w.windExp));
}

/* Runoff is a coastal band, not a shelf-wide event. */
function rainReach(offshoreKm, type, W) {
  const w = W || WEIGHTS;
  const offKm = offshoreKm != null ? offshoreKm
    : (type === 'estuarine' ? 0.5 : type === 'oceanic' ? 60 : 6);
  return Math.exp(-offKm / w.rainDecayKm);
}

function spotRange(spot) {
  const type = spot.type || 'shelf';
  return {
    vMax: spot.vMax != null ? spot.vMax
      : (type === 'oceanic' ? 32 : type === 'estuarine' ? 10 : 20),
    vMin: spot.vMin != null ? spot.vMin
      : (type === 'oceanic' ? 9 : type === 'estuarine' ? 1 : 3)
  };
}

function visBand(v) {
  return VIS_BANDS.find(b => v < b.max) || VIS_BANDS[3];
}

/* Offshore ceiling, shared by shelf and estuarine. */
function ceiling(f, spot, W) {
  const w = W || WEIGHTS;
  const type = spot.type || 'shelf';
  const reach = rainReach(spot.offshoreKm, type, w);
  return clamp(
    w.base
    - w.wStir * Math.min(1.6, f.stirLag || 0)
    - w.wWind * windMix(f.mixKmh, w)
    - w.wEkman * (f.ekman || 0)
    + w.wSst * clamp(f.sstAnom || 0, -2, 2)
    - reach * Math.min(w.rainCap, (f.rain72 || 0) * w.rainPerMm)
    + (f.season || 0),
    0, 100);
}

/* ---- the prediction ----
 *
 * f: prepared features
 *      stirLag, mixKmh, ekman, sstAnom, rain72, season
 *      tideQ, plume   (estuarine only; tideQ defaults to the 0.06 closed gate)
 * spot: type, offshoreKm, vMin, vMax
 *
 * Returns a full component trace, not just a number. A final-value match can
 * hide two errors cancelling, so equivalence is checked stage by stage.
 */
/* ---- the split: environmental state, then application heads ----
 *
 * `opticalState` answers "what is the water doing", with no notion of a diver,
 * a target or a direction. `visibilityHead` converts that into recreational
 * diver visibility. Other heads can sit alongside it later without the
 * environmental layer having to change: charter threshold probability, camera
 * imaging range, sediment plume risk, light at depth.
 *
 * The reason for the boundary is that visibility is not one quantity. A
 * spearfisher looking down through the column, a scuba diver judging a
 * horizontal target and a camera on an ROV are three different questions off
 * the same water. Baking one of them into the state makes the others a rewrite.
 *
 * `predict` remains as the combined call, so existing callers are unaffected.
 */

function opticalState(f, spot, W) {
  const w = W || WEIGHTS;
  const type = spot.type || 'shelf';

  const stirLag = Math.min(1.6, f.stirLag || 0);
  const wm = windMix(f.mixKmh, w);
  const reach = rainReach(spot.offshoreKm, type, w);
  const rainTerm = reach * Math.min(w.rainCap, (f.rain72 || 0) * w.rainPerMm);
  const ceil = ceiling(f, spot, w);
  const est = type !== 'oceanic' && type !== 'shelf';

  /* `clarity` is the branch-resolved 0-100 water clarity of the site, before
     any question about who is looking at it or how. This is the number a head
     consumes. */
  let clarity, driver;

  if (type === 'oceanic') {
    // 80 km offshore: clear by default, only swell and settling really bite
    clarity = clamp(w.oceanicBase - w.oceanicStir * stirLag - w.oceanicWind * wm, 0, 100);
    driver = stirLag > 0.7 ? 'swell stirring the bottom'
      : wm > 0.5 ? 'wind mixing the surface' : 'settled';
  } else if (type === 'shelf') {
    clarity = ceil;
    driver = stirLag > 0.8 ? 'swell stirring the bottom'
      : wm > 0.55 ? 'wind mixing the water column'
      : ((f.rain72 || 0) * reach) > 6 ? 'catchment runoff'
      : (f.ekman || 0) > 0.3 ? 'northerly, some upwelling risk'
      : (f.ekman || 0) < -0.2 ? 'southerly, clean water in' : 'settled';
  } else {
    // estuarine entrance: flood-window gate, then plume return
    const tideQ = f.tideQ != null ? f.tideQ : 0.06;
    const plume = f.plume || 0;
    clarity = clamp(ceil * Math.max(w.estTideFloor, tideQ) * (1 - w.estPlume * plume), 0, 100);
    driver = tideQ < 0.25 ? 'wrong tide state'
      : plume > 0.3 ? 'northerly pushed the ebb plume back south'
      : ceil < 45 ? 'offshore water is dirty, caps the window'
      : 'flood window on clean water';
  }

  /* Calculation trace. Field names match the exporter schema so observation,
     hindcast and reference exporters can record this verbatim.

     Both raw and clamped values are returned for the three clamped inputs. A
     clamp hides disagreement: two implementations receiving stirLag 2.4 and
     3.1 both emit 1.6, and a trace carrying only the clamped value would call
     that equivalent. */
  return {
    engine: ENGINE_VERSION,
    branch: type,

    stirLag: f.stirLag || 0,
    stirLagClamped: stirLag,
    windMix: wm,
    ekman: f.ekman || 0,
    sstAnom: f.sstAnom || 0,
    sstAnomClamped: clamp(f.sstAnom || 0, -2, 2),
    rain72mm: f.rain72 || 0,
    rainReach: reach,
    rainTerm,
    seasonAdj: f.season || 0,

    offshoreCeiling: ceil,
    tideQ: est ? (f.tideQ != null ? f.tideQ : 0.06) : null,
    plume: est ? (f.plume || 0) : null,

    clarity,
    driver
  };
}

/* Recreational diver visibility. One head among several, not the output.
 *
 * NOTE ON SCOPE. This maps clarity to a single horizontal-ish distance in
 * metres. It has no vertical structure, so it cannot answer the spearfishing
 * questions (looking down through the column, layer depth, water-column
 * uniformity) or anything requiring beam attenuation. Those need mixed-layer
 * depth and stratification, which the model does not yet carry. Do not add
 * empty heads for them: add the state first. */
function visibilityHead(state, spot, W) {
  const w = W || WEIGHTS;
  const { vMin, vMax } = spotRange(spot);
  const vis = state.clarity;
  return {
    visibilityIndex: vis,
    visibilityMetres: vMin + (vMax - vMin) * Math.pow(Math.max(0, vis) / 100, w.metresExp),
    visibilityBand: visBand(vis).label,
    reason: state.driver,
    vMin, vMax
  };
}

/* Combined call. Unchanged return shape, so existing callers are unaffected. */
function predict(f, spot, W) {
  const state = opticalState(f, spot, W);
  const head = visibilityHead(state, spot, W);
  const out = Object.assign({}, state, head);
  delete out.clarity;          // exposed as visibilityIndex by the head
  delete out.driver;           // exposed as reason by the head
  return out;
}

return { orbitalVel, bedStir, windMix, rainReach, ceiling,
         opticalState, visibilityHead, predict,
         visBand, spotRange, BED, VIS_BANDS, WEIGHTS, ENGINE_VERSION };
});
