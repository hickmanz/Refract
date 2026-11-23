<template>
  <!-- the form that starts a sweep (Run tab) -->
  <section v-if="mode === 'form'" class="run-box">
    <h4>Parameter sweep</h4>
    <p class="muted small">
      Simulate the same design with several values of one parameter, for example TiO₂ loading from 0.02 to 0.3 %, and see efficiency, uniformity and colour against it.
      Your setup is not changed.
    </p>

    <p v-if="!diffusers.length" class="muted small">Give the light pipe a material in the Parts tab first.</p>
    <template v-else>
      <label class="mini">
        <span>Part</span>
        <select v-model="partId">
          <option v-for="d in diffusers" :key="d.inst.id" :value="d.inst.id">{{ d.inst.name }} ({{ d.material }})</option>
        </select>
      </label>
      <label class="mini">
        <span>Parameter</span>
        <select v-model="parameter">
          <option v-for="p in PARAMS" :key="p.id" :value="p.id">{{ p.label }} ({{ p.unit }})</option>
        </select>
      </label>
      <div class="inline">
        <label class="mini narrow"><span>From</span><input v-model.number="from" type="number" step="any" /></label>
        <label class="mini narrow"><span>To</span><input v-model.number="to" type="number" step="any" /></label>
        <label class="mini narrow"><span>Steps</span><input v-model.number="steps" type="number" min="2" max="24" step="1" /></label>
        <label class="mini"><span>Spacing</span>
          <select v-model="spacing"><option value="log">logarithmic</option><option value="lin">linear</option></select>
        </label>
      </div>
      <p class="muted small">Values: {{ values.map((v) => fmt(v)).join(", ") }}. {{ values.length }} runs of {{ rays.toLocaleString() }} rays per channel.</p>
      <label class="mini">
        <span>Rays per channel</span>
        <select v-model.number="rays"><option v-for="r in [50000, 100000, 200000, 500000, 1000000, 2000000, 5000000]" :key="r" :value="r">{{ r.toLocaleString() }}</option></select>
      </label>
      <button class="btn primary" :disabled="busy || values.length < 2 || !ws.check.value" @click="start"><Play :size="15" /> Run sweep</button>
      <p v-if="error" class="error small">{{ error }}</p>
    </template>
  </section>

  <!-- the sweeps that were run, with their charts (Results tab) -->
  <section v-else-if="sweeps.length" class="run-box">
    <h4>Parameter sweeps</h4>
    <div v-for="sw in sweeps" :key="sw.id" class="sweep-card">
      <div class="part-head">
        <strong class="small">{{ sw.label }} sweep, {{ sw.part }}</strong>
        <span class="spacer" />
        <span v-if="sw.pending" class="muted small">{{ sw.done }} / {{ sw.runs.length }} done</span>
        <button class="icon-btn" title="Delete this sweep" @click="removeSweep(sw)"><Trash2 :size="14" /></button>
      </div>
      <div v-if="sw.pending" class="progress thin"><div class="progress-bar" :style="{ width: (sw.done / sw.runs.length) * 100 + '%' }" /></div>
      <template v-if="sw.points.length > 1">
        <LineChart title="Efficiency and uniformity" :series="sw.series" :x-label="sw.unit" y-label="%" />
        <LineChart title="Colour spread (max Δu′v′)" :series="sw.colorSeries" :x-label="sw.unit" y-label="Δu′v′" />
      </template>
      <table class="data-table compact">
        <thead><tr><th>{{ sw.label }}</th><th>Efficiency</th><th>Min/max</th><th>Hotspots</th><th>Δu′v′</th><th /></tr></thead>
        <tbody>
          <tr v-for="r in sw.runs" :key="r.id" :class="{ clickable: r.status === 'ready' }" @click="r.status === 'ready' && $emit('open', r.id)">
            <td>{{ fmt(r.sweep!.value) }} {{ sw.unit }}</td>
            <template v-if="det(r)">
              <td>{{ fmtPct(det(r)!.efficiency) }}</td>
              <td>{{ fmtPct(det(r)!.minOverMax, 0) }}</td>
              <td>{{ det(r)!.hotspots }}</td>
              <td>{{ det(r)!.maxDuv == null ? "–" : det(r)!.maxDuv!.toFixed(3) }}</td>
            </template>
            <template v-else><td colspan="4" class="muted">{{ r.status }}</td></template>
            <td />
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { Play, Trash2 } from "lucide-vue-next";
import { api, type RunMeta, type SweepInfo } from "../api";
import { useWorkspace } from "../composables/useWorkspace";
import { fmtPct } from "../utils/colormap";
import { isVolumeMaterial, partPattern, resolvePart } from "../utils/setup";
import LineChart from "./LineChart.vue";

defineProps<{ mode: "form" | "results" }>();
const emit = defineEmits<{ open: [id: string]; started: [] }>();
const ws = useWorkspace();

const PARAMS = [
  { id: "tio2_wt_pct", label: "TiO₂ loading", unit: "wt %", from: 0.002, to: 0.2, spacing: "log" },
  { id: "diameter_um", label: "TiO₂ particle diameter", unit: "µm", from: 0.15, to: 0.6, spacing: "lin" },
  { id: "mus", label: "Scattering coefficient", unit: "1/mm", from: 0.1, to: 5, spacing: "log" },
];
const parameter = ref("tio2_wt_pct");
const from = ref(0.002);
const to = ref(0.2);
const steps = ref(6);
const spacing = ref<"log" | "lin">("log");
const rays = ref(100000);
const busy = ref(false);
const error = ref("");
const partId = ref("");

watch(parameter, (p) => {
  const info = PARAMS.find((x) => x.id === p)!;
  from.value = info.from;
  to.value = info.to;
  spacing.value = info.spacing as "log" | "lin";
});

const diffusers = computed(() => {
  const s = ws.setup.value;
  if (!s) return [];
  return ws.parts.value
    .map((inst) => ({ inst, eff: resolvePart(inst, s.parts) }))
    .filter((x) => x.eff.enabled && isVolumeMaterial(ws.library.value, x.eff.material))
    .map((x) => ({ inst: x.inst, material: ws.library.value?.materials[x.eff.material!]?.name ?? x.eff.material! }));
});
watch(diffusers, (d) => {
  if (!d.some((x) => x.inst.id === partId.value)) partId.value = d[0]?.inst.id ?? "";
}, { immediate: true });

const values = computed(() => {
  const n = Math.min(Math.max(Math.round(steps.value) || 2, 2), 24);
  const a = from.value;
  const b = to.value;
  if (!Number.isFinite(a) || !Number.isFinite(b)) return [];
  const out: number[] = [];
  for (let i = 0; i < n; i++) {
    const t = i / (n - 1);
    out.push(spacing.value === "log" && a > 0 && b > 0 ? a * Math.pow(b / a, t) : a + (b - a) * t);
  }
  return out.map((v) => Number(v.toPrecision(3)));
});
const fmt = (v: number) => Number(v.toPrecision(3)).toString();
const det = (r: RunMeta) => r.summary?.states?.[0]?.detectors?.[0];

async function start() {
  const inst = ws.parts.value.find((p) => p.id === partId.value);
  if (!inst) return;
  busy.value = true;
  error.value = "";
  try {
    await ws.saveNow();
    const p = PARAMS.find((x) => x.id === parameter.value)!;
    await api.createSweep(ws.rid.value, { name: p.label, parameter: parameter.value, part: partPattern(inst, ws.parts.value), values: values.value, settings: { rays: rays.value } });
    await ws.loadRuns();
    emit("started");
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    busy.value = false;
  }
}

interface SweepGroup extends SweepInfo {
  runs: RunMeta[];
  points: { x: number; eff: number; uni: number | null; duv: number | null }[];
  pending: boolean;
  done: number;
  series: { label: string; color: string; x: number[]; y: (number | null)[] }[];
  colorSeries: { label: string; color: string; x: number[]; y: (number | null)[] }[];
}
const sweeps = computed<SweepGroup[]>(() => {
  const groups = new Map<string, RunMeta[]>();
  for (const r of ws.runs.value) if (r.sweep) groups.set(r.sweep.id, [...(groups.get(r.sweep.id) ?? []), r]);
  const out: SweepGroup[] = [];
  for (const [, runs] of groups) {
    runs.sort((a, b) => a.sweep!.value - b.sweep!.value);
    const info = runs[0].sweep!;
    const ready = runs.filter((r) => r.status === "ready" && det(r));
    const points = ready.map((r) => ({ x: r.sweep!.value, eff: det(r)!.efficiency * 100, uni: det(r)!.minOverMax == null ? null : det(r)!.minOverMax! * 100, duv: det(r)!.maxDuv ?? null }));
    out.push({
      ...info,
      runs,
      points,
      pending: runs.some((r) => r.status === "queued" || r.status === "running"),
      done: runs.filter((r) => r.status === "ready" || r.status === "failed" || r.status === "cancelled").length,
      series: [
        { label: "efficiency", color: "#16a34a", x: points.map((p) => p.x), y: points.map((p) => p.eff) },
        { label: "uniformity (min/max)", color: "#2563eb", x: points.map((p) => p.x), y: points.map((p) => p.uni) },
      ],
      colorSeries: [{ label: "max Δu′v′", color: "#db2777", x: points.map((p) => p.x), y: points.map((p) => p.duv) }],
    });
  }
  return out.sort((a, b) => Math.max(...b.runs.map((r) => r.created)) - Math.max(...a.runs.map((r) => r.created)));
});

async function removeSweep(sw: SweepGroup) {
  if (!confirm(`Delete the ${sw.runs.length} runs of this sweep?`)) return;
  for (const r of sw.runs) await api.deleteRun(r.id);
  await ws.loadRuns();
}
</script>
