<template>
  <div class="compare">
    <div class="res-bar">
      <strong>Compare</strong>
      <button class="btn small" :class="{ primary: mode === 'pair' }" @click="mode = 'pair'">Two runs (maps)</button>
      <button class="btn small" :class="{ primary: mode === 'many' }" @click="mode = 'many'">Several runs (table)</button>
      <template v-if="mode === 'pair'">
        <span class="chip led">A</span> <span class="small">{{ ownLabel }}</span>
        <span class="chip glass">B</span>
        <select :value="otherId" @change="$emit('change-other', ($event.target as HTMLSelectElement).value)">
          <option value="">Choose a run…</option>
          <option v-for="r in candidates" :key="r.id" :value="r.id">{{ r.label }}</option>
        </select>
      </template>
      <span class="spacer" />
      <button class="btn small" @click="$emit('close')"><X :size="14" /> Close comparison</button>
    </div>
    <MultiCompare v-if="mode === 'many'" :run-id="runId" :state="state" :detector="detector" :analysis="analysis" :candidates="allRuns" />
    <p v-if="mode === 'pair' && !candidates.length" class="muted small">There is no other finished run in this project yet. Run another revision or variant (or the same revision with different materials) first.</p>
    <div v-if="mode === 'pair' && error" class="notice warn"><AlertTriangle :size="15" /><span>{{ error }}</span></div>

    <template v-if="mode === 'pair' && data">
      <div class="res-grid stack">
        <section class="res-card">
          <HeatMap :axes="false" title="A" :values="data.maps.a" :valid="data.maps.validA" :vmin="options.log ? data.maps.vmax * 0.001 : 0" :vmax="data.maps.vmax" :unit="data.unit" :u-mm="mapAspect" :v-mm="1" :log="options.log" />
        </section>
        <section class="res-card">
          <HeatMap :axes="false" title="B" :values="data.maps.b" :valid="data.maps.validB" :vmin="options.log ? data.maps.vmax * 0.001 : 0" :vmax="data.maps.vmax" :unit="data.unit" :u-mm="mapAspect" :v-mm="1" :log="options.log" />
        </section>
        <section class="res-card">
          <HeatMap :axes="false" title="B − A" :values="data.maps.diff" :vmin="-data.maps.dmax" :vmax="data.maps.dmax" :unit="data.unit" :u-mm="mapAspect" :v-mm="1" diverging />
        </section>
      </div>
      <p class="muted small">Both maps share one colour scale and are resampled to the same normalised surface (length and width are scaled to fit).</p>

      <section class="res-card">
        <table class="data-table">
          <thead><tr><th>Metric</th><th>A</th><th>B</th><th>B − A</th></tr></thead>
          <tbody>
            <tr v-for="row in rows" :key="row.label">
              <td>{{ row.label }}</td>
              <td>{{ row.a }}</td>
              <td>{{ row.b }}</td>
              <td :class="row.cls">{{ row.delta }}</td>
            </tr>
          </tbody>
        </table>
      </section>

      <div class="res-grid">
        <section class="res-card">
          <LineChart title="Profile along the length (normalised position)" :series="profileSeries" x-label="% of length" :y-label="data.unit" />
        </section>
        <section class="res-card">
          <LineChart title="Intensity by angle (relative to own peak)" :series="angleSeries" x-label="degrees" y-label="relative intensity" />
        </section>
      </div>

      <section class="res-card">
        <div class="hm-title"><span>Where the light goes: A vs B</span></div>
        <table class="data-table">
          <thead><tr><th>Destination</th><th>A</th><th>B</th><th>B − A</th></tr></thead>
          <tbody>
            <tr v-for="l in lossRows" :key="l.name">
              <td>{{ l.name }}</td><td>{{ fmtPct(l.a) }}</td><td>{{ fmtPct(l.b) }}</td>
              <td :class="l.kind === 'exit' ? (l.b - l.a >= 0 ? 'good' : 'bad') : ''">{{ signed((l.b - l.a) * 100, 1) }} pts</td>
            </tr>
          </tbody>
        </table>
      </section>
    </template>
    <p v-else-if="mode === 'pair' && !otherId && candidates.length" class="muted center">Choose a second run to compare with.</p>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { AlertTriangle, X } from "lucide-vue-next";
import { api, type Analysis, type CompareResult } from "../api";
import { revisionName, useWorkspace } from "../composables/useWorkspace";
import { fmtNum, fmtPct } from "../utils/colormap";
import HeatMap from "./HeatMap.vue";
import LineChart from "./LineChart.vue";
import MultiCompare, { type RunCandidate } from "./MultiCompare.vue";

const props = defineProps<{ runId: string; otherId: string; state?: string; detector: number; analysis: Analysis; options: { log: boolean }; aspect?: number }>();
defineEmits<{ "change-other": [id: string]; close: [] }>();

const ws = useWorkspace();
const data = ref<CompareResult | null>(null);
const error = ref("");
const candidates = ref<{ id: string; label: string }[]>([]);
const ownLabel = ref("this run");

const allRuns = ref<RunCandidate[]>([]); // every finished run of the project, grouped by design and variant (for the several-runs table)
const mode = ref<"pair" | "many">("pair");
/** "Design › Variant" (just the variant's name when the project has one design). */
const variantName = (id?: string) => {
  const p = ws.project.value;
  const v = p?.variants?.find((x) => x.id === id);
  if (!v) return "Variant";
  const d = (p?.designs?.length ?? 0) > 1 ? p?.designs.find((x) => x.id === v.designId) : undefined;
  return d ? `${d.name} › ${v.name}` : v.name;
};

async function loadCandidates() {
  const revs = ws.project.value?.revisionList ?? (ws.meta.value ? [ws.meta.value] : []);
  const out: { id: string; label: string }[] = [];
  const everything: RunCandidate[] = [];
  for (const rev of revs) {
    try {
      const runs = await api.listRuns(rev.id);
      const vName = variantName(rev.variantId);
      const revName = revisionName(rev, revs);
      const multi = (ws.project.value?.variants?.length ?? 1) > 1;
      const label = `${multi ? `${vName} · ` : ""}${revName}`;
      for (const r of runs) {
        if (r.status !== "ready") continue;
        everything.push({ id: r.id, label: `${revName} · ${r.name}`, group: vName });
        if (r.id === props.runId) ownLabel.value = `${label} · ${r.name}`;
        else out.push({ id: r.id, label: `${label} · ${r.name}` });
      }
    } catch {
      /* ignore */
    }
  }
  candidates.value = out;
  allRuns.value = everything;
}
loadCandidates();

watch(
  () => [props.runId, props.otherId, props.state, props.detector, JSON.stringify(props.analysis)],
  async () => {
    data.value = null;
    if (!props.otherId) return;
    try {
      data.value = await api.compare(props.runId, props.otherId, { state: props.state, detector: props.detector, analysis: props.analysis });
      error.value = "";
    } catch (e) {
      error.value = (e as Error).message;
    }
  },
  { immediate: true },
);

const mapAspect = computed(() => props.aspect ?? (data.value ? data.value.maps.nu / data.value.maps.nv : 1));

function signed(x: number, d = 2) {
  return `${x >= 0 ? "+" : ""}${x.toFixed(d)}`;
}
interface Row { label: string; a: string; b: string; delta: string; cls: string }
const rows = computed<Row[]>(() => {
  const d = data.value;
  if (!d) return [];
  const a = d.a;
  const b = d.b;
  const mk = (label: string, av: number | undefined | null, bv: number | undefined | null, opts: { pct?: boolean; higherBetter?: boolean | null; digits?: number } = {}): Row => {
    if (av == null || bv == null) return { label, a: "–", b: "–", delta: "–", cls: "" };
    const diff = bv - av;
    const rel = av !== 0 ? (diff / Math.abs(av)) * 100 : 0;
    const better = opts.higherBetter == null ? "" : (diff > 0) === opts.higherBetter ? "good" : diff === 0 ? "" : "bad";
    const fa = opts.pct ? fmtPct(av) : fmtNum(av, opts.digits ?? 3);
    const fb = opts.pct ? fmtPct(bv) : fmtNum(bv, opts.digits ?? 3);
    const fd = opts.pct ? `${signed(diff * 100, 1)} pts` : `${signed(diff, 3)} (${signed(rel, 1)} %)`;
    return { label, a: fa, b: fb, delta: fd, cls: better };
  };
  return [
    mk("Efficiency (lm out / lm in)", a.efficiency, b.efficiency, { pct: true, higherBetter: true }),
    mk("Flux at the exit (lm)", a.exitLm, b.exitLm, { higherBetter: true }),
    mk(`Average (${d.unit})`, a.stats.mean, b.stats.mean, { higherBetter: true }),
    mk("Uniformity min / max", a.stats.minOverMax, b.stats.minOverMax, { pct: true, higherBetter: true }),
    mk("Uniformity min / avg", a.stats.minOverAvg, b.stats.minOverAvg, { pct: true, higherBetter: true }),
    mk("Variation (CV)", a.stats.cv, b.stats.cv, { pct: true, higherBetter: false }),
    mk("Hotspots", a.hotspots, b.hotspots, { higherBetter: false, digits: 0 }),
    mk("Correlated colour temperature (K)", a.color?.cct, b.color?.cct, { higherBetter: null, digits: 0 }),
    mk("Max colour difference Δu′v′", a.color?.maxDuv, b.color?.maxDuv, { higherBetter: false, digits: 4 }),
  ];
});

const lossRows = computed(() => {
  const d = data.value;
  if (!d) return [];
  const names = [...new Set([...d.a.loss, ...d.b.loss].map((l) => l.name))];
  return names.map((name) => {
    const la = d.a.loss.find((l) => l.name === name);
    const lb = d.b.loss.find((l) => l.name === name);
    return { name, kind: (la ?? lb)!.kind, a: la?.fraction ?? 0, b: lb?.fraction ?? 0 };
  });
});

const profileSeries = computed(() => {
  const d = data.value;
  if (!d) return [];
  const mk = (label: string, color: string, p: (number | null)[]) => ({ label, color, x: p.map((_, i) => (i / Math.max(p.length - 1, 1)) * 100), y: p });
  return [mk("A", "#2563eb", d.a.profileU), mk("B", "#ea580c", d.b.profileU)];
});
const angleSeries = computed(() => {
  const d = data.value;
  if (!d) return [];
  return [
    { label: "A", color: "#2563eb", x: d.a.angular.angles, y: d.a.angular.relative as (number | null)[] },
    { label: "B", color: "#ea580c", x: d.b.angular.angles, y: d.b.angular.relative as (number | null)[] },
  ];
});
</script>
