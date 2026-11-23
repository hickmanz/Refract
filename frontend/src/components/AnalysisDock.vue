<template>
  <aside class="analysis-dock" @click.stop @pointermove.stop>
    <div class="live-head">
      <strong>What to show</strong>
      <span class="spacer" />
      <button class="icon-btn" title="Hide this panel" @click="emit('close')"><X :size="15" /></button>
    </div>

    <!-- no run is open yet: say so and offer the finished runs -->
    <section v-if="!hasRun" class="layer empty-state">
      <div class="notice info"><Info :size="15" /><span>No results are open. The analysis shows a run's map, surfaces and rays on the model. Pick a run to start.</span></div>
      <ul v-if="runs.length" class="run-pick">
        <li v-for="r in runs" :key="r.id">
          <button class="run-pick-item" @click="emit('pick-run', r.id)">
            <strong>{{ r.name }}</strong>
            <span class="muted small">
              {{ fmtAgo(r.created) }} · {{ (r.raysTraced ?? r.settings.rays).toLocaleString() }} rays
              <span v-if="r.hasAnalysis === false" class="chip" title="Made without the light path analysis: only the exit map can be shown">map only</span>
            </span>
          </button>
        </li>
      </ul>
      <template v-else>
        <p class="muted small">There are no finished runs yet.</p>
        <button class="btn small" @click="emit('new-run')"><Play :size="14" /> Go to Runs</button>
      </template>
    </section>

    <template v-if="hasRun">
    <!-- what was picked in the results -->
    <div class="picked" :class="{ empty: !sel }">
      <template v-if="sel">
        <div class="picked-what">
          <span class="muted small">{{ kindLabel }}</span>
          <strong>{{ sel.label }}</strong>
        </div>
        <button class="btn small" title="Forget the selection and go back to the plain exit map" @click="av.resetLayers()"><RotateCcw :size="14" /> Reset</button>
      </template>
      <template v-else>
        <span class="muted small">Nothing picked. Press <strong>View in 3D</strong> on a surface, a route or a region in the results to look at it here.</span>
      </template>
      <button class="btn small" @click="emit('open-results')"><BarChart3 :size="14" /> Results</button>
    </div>

    <!-- ───────── the map on the exit surface ───────── -->
    <section class="layer">
      <h4 class="dock-h"><Map :size="14" /> Map on the exit surface</h4>
      <div class="seg">
        <button :class="{ on: av.layers.map === 'run' }" title="The exit map of the whole run" @click="av.layers.map = 'run'">The run</button>
        <button :class="{ on: av.layers.map === 'selection' }" :disabled="!sel?.footprint" :title="sel?.footprint ? 'Where the light of the selection lands' : 'The selection has no map of its own'" @click="av.layers.map = 'selection'">Selection</button>
        <button :class="{ on: av.layers.map === 'rays' }" :disabled="av.layers.rays === 'off'" :title="av.layers.rays === 'off' ? 'Turn the rays on first' : 'Where the rays that are shown land, with whatever filter they have'" @click="av.layers.map = 'rays'">Shown rays</button>
        <button :class="{ on: av.layers.map === 'off' }" @click="av.layers.map = 'off'">Off</button>
      </div>
      <label class="switch" :class="{ disabled: av.layers.map === 'off' }" title="Draw the colour scale of the map that is on the model in a corner of the 3D view">
        <input v-model="av.layers.showScale" type="checkbox" :disabled="av.layers.map === 'off'" /> Show the colour scale in the 3D view
      </label>
      <template v-if="(av.layers.map === 'selection' || av.layers.map === 'rays') && av.modelMap.value">
        <div class="ramp">
          <span>0</span>
          <i class="grad" :style="{ background: turboCss }" />
          <span>{{ fmtNum(av.modelMap.value.vmax) }} {{ av.modelMap.value.unit }}</span>
        </div>
        <label class="switch" title="Use the colour scale of the exit map of the run, so a weak contribution looks weak">
          <input v-model="av.layers.sameScale" type="checkbox" /> Same scale as the exit map of the run
        </label>
      </template>
      <p v-if="av.layers.map === 'rays' && face && face.group" class="muted small">Only the shown rays that went through <strong>{{ face.group.name }}</strong> (the surface you inspected).</p>
      <p v-else-if="av.layers.map === 'rays' && face" class="muted small">The surface you inspected is not in a group, so the map cannot follow it.</p>
      <p v-if="av.layers.map === 'rays' && noExitRays" class="muted small">These rays never reach the exit surface, so there is nothing to show. Pick “reach the exit” (or any) under Ray options.</p>
    </section>

    <!-- ───────── surfaces ───────── -->
    <section class="layer">
      <h4 class="dock-h"><Layers :size="14" /> Surfaces</h4>
      <div class="seg">
        <button :class="{ on: av.layers.surfaces === 'selection' }" :disabled="!sel?.spots.length" :title="sel?.spots.length ? 'Light up the surfaces of the selection' : 'The selection has no surfaces to light up'" @click="av.layers.surfaces = 'selection'">Selected</button>
        <button :class="{ on: av.layers.surfaces === 'heat' }" title="Paint every surface by how much light it gets" @click="av.layers.surfaces = 'heat'">Heat map</button>
        <button :class="{ on: av.layers.surfaces === 'off' }" @click="av.layers.surfaces = 'off'">Off</button>
      </div>
      <div v-if="av.layers.surfaces === 'selection' && sel?.spots.length" class="chips">
        <span v-for="(s, i) in sel.spots" :key="i" class="spot-chip"><i :style="{ background: s.color }" /> {{ s.label }}</span>
      </div>
      <template v-if="av.layers.surfaces === 'heat'">
        <label class="mini">
          <span>Paint every surface by</span>
          <select v-model="av.hitQuantity.value">
            <option value="incident">light that hits it</option>
            <option value="absorbed">light it absorbs</option>
            <option value="reflected">light it reflects</option>
            <option value="transmitted">light that passes through</option>
          </select>
        </label>
        <div v-if="av.hitError.value" class="notice warn"><AlertTriangle :size="15" /><span>{{ av.hitError.value }}</span></div>
        <div v-if="av.hitMap.value && av.hitQuantity.value" class="hit-legend">
          <div class="ramp">
            <span>0</span>
            <i class="grad" :style="{ background: `linear-gradient(to right, ${[0, 0.25, 0.5, 0.75, 1].map((t) => av.heatCss(t)).join(',')})` }" />
            <span>{{ densityText }}</span>
          </div>
          <p class="muted small">
            Brightness of a face = flux per area (% of the emitted flux per cm²).
            <template v-if="av.hitMap.value.total > 1">Counted over every hit, all faces together get {{ av.hitMap.value.total.toFixed(1) }}× the emitted flux (rays hit surfaces more than once).</template>
            <template v-else>All faces together: {{ fmtPct(av.hitMap.value.total, 1) }} of the emitted flux.</template>
            Hot spots show where the light piles up; dark faces are not doing anything.
          </p>
        </div>
      </template>
    </section>

    <!-- ───────── rays ───────── -->
    <section class="layer">
      <h4 class="dock-h"><Waypoints :size="14" /> Rays</h4>
      <label class="mini">
        <select v-model="av.layers.rays">
          <option value="off">Off</option>
          <option value="selection" :disabled="!sel?.rays">The rays of the selection</option>
          <option value="exit">All light that reaches the exit</option>
          <option value="absorbed">All light that is absorbed</option>
          <option value="escaped">All light that escapes elsewhere</option>
          <option v-if="av.layers.rays === 'custom'" value="custom">{{ av.rayLabel.value || "Other rays" }}</option>
        </select>
      </label>
      <div v-if="av.rayLoading.value" class="muted small"><div class="spinner small" /> Tracing the rays…</div>
      <div v-if="av.rayError.value" class="notice warn"><AlertTriangle :size="15" /><span>{{ av.rayError.value }}</span></div>

      <template v-if="av.rays.value && av.layers.rays !== 'off'">
        <p class="muted small ray-count">
          {{ av.rays.value.paths.length }} shown of {{ av.rays.value.matched.toLocaleString() }} matching rays<template v-if="av.rays.value.fluxFraction > 0">
            (carry {{ fmtPct(av.rays.value.fluxFraction, 0) }} of the exit light)</template>
        </p>

        <ul class="legend-list">
          <template v-if="av.style.color === 'led'">
            <li v-for="l in usedLeds" :key="l.id"><i :style="{ background: css(l.color) }" /> {{ l.name }}</li>
          </template>
          <template v-else-if="av.style.color === 'flux'">
            <li class="ramp"><span>strong</span><i class="grad" style="background: linear-gradient(to right, hsl(0 100% 50%), hsl(60 100% 50%), hsl(120 100% 50%), hsl(180 100% 50%), hsl(240 100% 50%))" /><span>1 / 1000</span></li>
          </template>
          <template v-else-if="av.style.color === 'bounce'">
            <li class="ramp"><span>first</span><i class="grad" style="background: linear-gradient(to right, hsl(259 100% 50%), hsl(180 100% 50%), hsl(100 100% 50%), hsl(40 100% 50%), hsl(0 100% 50%))" /><span>last</span></li>
          </template>
          <template v-else-if="av.style.color === 'event'">
            <li v-for="e in EVENTS.slice(1)" :key="e.name"><i :style="{ background: css(e.color) }" /> {{ e.label }}</li>
          </template>
          <template v-else-if="av.style.color === 'group'">
            <li v-for="g in usedGroups" :key="g.index"><i :style="{ background: av.groupCss(g.index) }" /> {{ g.name }}</li>
            <li><i style="background: #9ca3af" /> not on a tagged surface</li>
          </template>
          <template v-else>
            <li><i style="background: #06b6d4" /> arrives at the selected surface</li>
            <li><i style="background: #f97316" /> leaves the selected surface</li>
            <li v-if="av.style.showOthers"><i style="background: #9ca3af" /> the rest of the ray</li>
          </template>
        </ul>

        <details class="more">
          <summary>Ray options</summary>
          <div class="dock-grid">
            <label class="mini">
              <span>Outcome</span>
              <select v-model="av.form.outcome" @change="av.fetchRays()">
                <option value="">any</option>
                <option value="exit">reach the exit</option>
                <option value="absorbed">absorbed</option>
                <option value="escaped">escape elsewhere</option>
                <option value="lost">lost / dropped</option>
              </select>
            </label>
            <label v-if="detectorChoices.length > 1" class="mini" title="Only the light that reached this exit surface or receiver. Rays are also cut at it when they pass another one first.">
              <span>Reaching</span>
              <select v-model.number="av.form.detector" @change="onDetector">
                <option :value="-1">any</option>
                <option v-for="d in detectorChoices" :key="d.index" :value="d.index">{{ d.name }}</option>
              </select>
            </label>
            <label class="mini">
              <span>LED</span>
              <select v-model="av.form.led" @change="av.fetchRays()">
                <option value="">all</option>
                <option v-for="l in leds" :key="l.id" :value="l.id">{{ l.name }}</option>
              </select>
            </label>
            <label class="mini">
              <span>Touched surface</span>
              <select v-model.number="av.form.touched" @change="av.fetchRays()">
                <option :value="-1">any</option>
                <option v-for="g in groupChoices" :key="g.index" :value="g.index">{{ g.name }}</option>
              </select>
            </label>
            <label class="mini">
              <span>Rays drawn</span>
              <select v-model.number="av.form.count" @change="av.fetchRays()">
                <option v-for="n in [50, 100, 200, 400, 800, 1500, 3000]" :key="n" :value="n">{{ n }}</option>
              </select>
            </label>
            <label class="mini">
              <span>Bounces from</span>
              <input v-model.number="av.form.minBounce" type="number" min="0" max="500" @change="av.fetchRays()" />
            </label>
            <label class="mini">
              <span>to (0 = no limit)</span>
              <input v-model.number="av.form.maxBounce" type="number" min="0" max="500" @change="av.fetchRays()" />
            </label>
          </div>

          <hr />
          <div class="dock-grid">
            <label class="mini">
              <span>Colour by</span>
              <select v-model="av.style.color">
                <option value="led">LED</option>
                <option value="flux">Remaining flux</option>
                <option value="bounce">Bounce number</option>
                <option value="event">What happens at the end of the segment</option>
                <option value="group">Surface it came from</option>
                <option value="face" :disabled="!av.inspected.value">Selected surface (in / out)</option>
              </select>
            </label>
            <label class="mini" title="Brighter where many rays overlap">
              <span>Display</span>
              <select v-model="av.style.density">
                <option :value="false">Lines</option>
                <option :value="true">Glow (density)</option>
              </select>
            </label>
          </div>
          <label class="switch" title="A ray that reaches the exit surface may carry on inside afterwards; this hides that part">
            <input v-model="av.style.stopAtExit" type="checkbox" /> Stop each ray at the detector
          </label>
          <label class="slider-row" title="Draw only the first segments of every ray">
            <span class="small">Bounces shown</span>
            <input v-model.number="av.style.maxBounce" type="range" min="0" :max="av.longestRay.value" step="1" />
            <strong>{{ av.style.maxBounce === 0 ? "all" : av.style.maxBounce }}</strong>
          </label>
          <label v-if="!av.style.density" class="slider-row">
            <span class="small">Opacity</span>
            <input v-model.number="av.style.opacity" type="range" min="0.1" max="1" step="0.05" />
            <strong>{{ Math.round(av.style.opacity * 100) }} %</strong>
          </label>
          <label v-else class="slider-row">
            <span class="small">Glow gain</span>
            <input v-model.number="av.style.densityGain" type="range" min="0.05" max="1" step="0.05" />
            <strong>{{ av.style.densityGain.toFixed(2) }}</strong>
          </label>
          <label v-if="av.style.color === 'face'" class="switch" title="Off: only the parts of the rays that arrive at or leave the selected surface are drawn">
            <input v-model="av.style.showOthers" type="checkbox" /> Also draw the rest of each ray (faint grey)
          </label>
        </details>
      </template>
    </section>

    <!-- ───────── click a surface to inspect it ───────── -->
    <section class="layer">
      <h4 class="dock-h"><MousePointerClick :size="14" /> Inspect a surface</h4>
      <p v-if="!face && !av.inspectError.value" class="muted small">Click any face of the model to see what it does.</p>
      <div v-if="av.inspectError.value" class="notice warn"><AlertTriangle :size="15" /><span>{{ av.inspectError.value }}</span></div>

      <div v-if="face" class="face-card">
        <div class="dock-sub">
          <strong>{{ face.face.partName }} · face {{ face.face.face }}</strong>
          <span class="muted small">
            {{ face.face.areaMm2.toFixed(1) }} mm²
            <template v-if="face.group"> · group <i class="dot" :style="{ background: face.group.color }" /> {{ face.group.name }}</template>
            <template v-else> · not in any group</template>
          </span>
          <span v-if="faceWhere" class="muted small">{{ faceWhere }}</span>
        </div>
        <BarList
          :items="[
            { label: 'Arrives (of emitted flux)', value: face.incident, color: '#06b6d4' },
            { label: 'Absorbed', value: face.absorbed, color: '#ef4444' },
            { label: 'Reflected', value: face.reflected, color: '#3b82f6' },
            { label: 'Passes through', value: face.transmitted, color: '#10b981' },
          ]"
          :scale="flowScale"
        />
        <FootprintMap v-if="face.group && ctx" v-model:mode="footMode" :ctx="ctx" kind="group" :id="face.group.index" modes />
        <div class="dock-row">
          <button class="btn primary small" :disabled="av.rayLoading.value" @click="raysAtFace"><Waypoints :size="14" /> Show the rays at this surface</button>
          <button class="btn small" @click="av.clearInspection()"><X :size="14" /> Done</button>
        </div>

        <template v-if="face.comesFrom?.length">
          <h4 class="dock-h">Where its light comes from</h4>
          <BarList :items="flow(face.comesFrom)" />
          <h4 class="dock-h">Where it goes next</h4>
          <BarList :items="flow(face.goesTo ?? [])" />
        </template>
        <p v-else class="muted small">
          This face is not in a surface group, so Refract only counted the totals above. Add it to a group in the Groups tab, run again, and you will see
          where its light comes from, where it goes, and where it lands on the exit surface.
        </p>
      </div>
    </section>
    </template>
  </aside>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";
import { AlertTriangle, BarChart3, Info, Layers, Map, MousePointerClick, Play, RotateCcw, Waypoints, X } from "lucide-vue-next";
import type { FaceReport, FlowItem, RaysFilter, RunMeta } from "../api";
import { EVENTS, LED_PALETTE, injectAnalysisView, type AnalysisCtx } from "../composables/useAnalysisView";
import { useWorkspace } from "../composables/useWorkspace";
import { describeSurface } from "../utils/analysisText";
import { cssGradient, fmtNum, fmtPct } from "../utils/colormap";
import { fmtAgo } from "../utils/format";
import BarList from "./BarList.vue";
import FootprintMap from "./analysis/FootprintMap.vue";

defineProps<{ ctx: AnalysisCtx | null; hasRun: boolean; runs: RunMeta[] }>();
const emit = defineEmits<{ close: []; "open-results": []; "pick-run": [runId: string]; "new-run": [] }>();
const av = injectAnalysisView();
const ws = useWorkspace();

const sel = computed(() => av.selection.value);
const kindLabel = computed(() => (sel.value?.kind === "surface" ? "Surface" : sel.value?.kind === "route" ? "Route" : "Region of the exit surface"));
/** The shown rays are the ones that are absorbed, escape or are lost: none of them lands on the exit surface. */
const noExitRays = computed(() => ["absorbed", "escaped", "lost"].includes(av.shownFilter.value?.outcome ?? ""));
const footMode = ref<"last" | "touched">("touched");
const turboCss = cssGradient("turbo").replace("to top", "to right");
const css = (n: number) => `#${n.toString(16).padStart(6, "0")}`;
const leds = computed(() => (ws.setup.value?.leds ?? []).map((l) => ({ id: l.id, name: l.name })));
/** The exit surfaces and receivers of the setup, to choose which one "reaches the exit" means. */
const detectorChoices = computed(() => (ws.setup.value?.detectors ?? []).map((d, index) => ({ index, name: d.name || `Detector ${index + 1}` })));
function onDetector() {
  // choosing a detector means "light that reaches it": the outcome follows, so the choice does what it says
  if (av.form.detector >= 0 && !av.form.outcome) av.form.outcome = "exit";
  void av.fetchRays();
}
const usedLeds = computed(() => {
  const seen = new Set((av.rays.value?.paths ?? []).map((p) => p.led));
  return leds.value.map((l, i) => ({ ...l, color: LED_PALETTE[i % LED_PALETTE.length] })).filter((l) => seen.has(l.id));
});
const groupChoices = computed(() => av.groups.value.filter((g) => g.touchedFraction > 0 || g.incident > 0).sort((a, b) => b.incident - a.incident));
const usedGroups = computed(() => {
  const seen = new Set<number>();
  for (const p of av.rays.value?.paths ?? []) for (const g of p.group) if (g >= 0) seen.add(g);
  return av.groups.value.filter((g) => seen.has(g.index)).slice(0, 14);
});

// ── the surface inspector ────────────────────────────────────────────────────────
const face = computed<FaceReport | null>(() => av.inspected.value);
const flowScale = computed(() => 1 / Math.max(face.value?.incident ?? 1, 1e-9));
const flow = (items: FlowItem[]) => items.slice(0, 8).map((f) => ({ label: f.name, value: f.fraction, color: f.color }));
const faceWhere = computed(() => {
  const gi = av.groups.value.find((g) => g.index === face.value?.group?.index);
  return gi ? describeSurface(gi.geometry, av.frame.value) : "";
});
async function raysAtFace() {
  const f = face.value;
  if (!f) return;
  const filter: RaysFilter = f.group ? { touched: f.group.index } : {};
  await av.showRays(filter, `Rays that hit ${f.face.partName} · face ${f.face.face}`, { gface: f.face.gface, color: "face" });
}

const densityText = computed(() => {
  const h = av.hitMap.value;
  if (!h) return "";
  const perCm2 = h.maxDensity * 100 * 100; // fraction per mm2 -> % per cm2
  return `${perCm2 < 10 ? perCm2.toFixed(2) : perCm2.toFixed(0)} %/cm²`;
});
</script>

<style scoped>
.picked { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; padding: 6px 8px; border: 1px solid var(--accent-6); border-radius: var(--radius-md); background: var(--accent-2); }
.picked.empty { border-color: var(--neutral-4); background: var(--neutral-2); }
.picked-what { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 1px; font-size: 0.82rem; }
.layer { display: flex; flex-direction: column; gap: 6px; padding-top: 8px; border-top: 1px dashed var(--neutral-4); }
.seg { display: flex; gap: 4px; }
.seg button {
  flex: 1; padding: 4px 6px; border: 1px solid var(--neutral-4); border-radius: var(--radius-md); background: var(--neutral-1);
  color: var(--neutral-11); font: inherit; font-size: 0.78rem; font-weight: 600; cursor: pointer;
}
.seg button:disabled { opacity: 0.45; cursor: not-allowed; }
.seg button.on { background: var(--accent-3); border-color: var(--accent-7); color: var(--neutral-12); }
.chips { display: flex; flex-wrap: wrap; gap: 4px 10px; font-size: 0.78rem; }
.dock-row { display: flex; flex-wrap: wrap; gap: 6px; }
.dock-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 6px 8px; }
.dock-sub { display: flex; flex-direction: column; gap: 1px; }
.dock-h { margin: 0; font-size: 0.8rem; color: var(--neutral-11); display: flex; align-items: center; gap: 5px; }
.face-card .dock-h { margin-top: 4px; }
.face-card { display: flex; flex-direction: column; gap: 6px; }
.dot { display: inline-block; width: 10px; height: 10px; border-radius: 3px; vertical-align: -1px; }
.ray-count { margin: 0; }
.more { display: flex; flex-direction: column; gap: 6px; }
.more > summary { cursor: pointer; font-size: 0.78rem; font-weight: 600; color: var(--neutral-11); margin-bottom: 6px; }
.legend-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 2px; font-size: 0.76rem; max-height: 150px; overflow-y: auto; }
.legend-list li { display: flex; align-items: center; gap: 6px; }
.legend-list i:not(.grad) { width: 18px; height: 4px; border-radius: 2px; flex: none; }
.ramp { display: flex; align-items: center; gap: 6px; font-size: 0.72rem; color: var(--neutral-10); }
.grad { flex: 1; height: 8px; border-radius: 4px; border: 1px solid var(--neutral-4); display: inline-block; }
.hit-legend { display: flex; flex-direction: column; gap: 4px; }
.run-pick { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 4px; max-height: 320px; overflow-y: auto; }
.run-pick-item {
  width: 100%; display: flex; flex-direction: column; align-items: flex-start; gap: 1px; padding: 6px 8px; text-align: left;
  border: 1px solid var(--neutral-4); border-radius: var(--radius-md); background: var(--neutral-1); color: inherit; font: inherit; font-size: 0.82rem; cursor: pointer;
}
.run-pick-item:hover { background: var(--accent-3); border-color: var(--accent-7); }
.switch.disabled { opacity: 0.5; }
.spot-chip { display: inline-flex; align-items: center; gap: 5px; }
.spot-chip i { width: 10px; height: 10px; border-radius: 3px; display: inline-block; }
</style>
