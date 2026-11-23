<template>
  <div class="multi-compare">
    <section class="res-card">
      <div class="hm-title"><span>Runs to compare</span><span class="muted small">pick 2 to 12; the baseline is what the deltas are measured against</span></div>
      <div v-if="!groups.length" class="muted small">There is no finished run in this project yet.</div>
      <div v-for="g in groups" :key="g.name" class="mc-group">
        <div class="mc-group-name">{{ g.name }}</div>
        <div v-for="c in g.items" :key="c.id" class="mc-pick">
          <label class="mc-pick">
            <input type="checkbox" :checked="selected.includes(c.id)" :disabled="!selected.includes(c.id) && selected.length >= 12" @change="toggle(c.id)" />
            <i class="mc-dot" :style="{ background: selected.includes(c.id) ? colorOf(c.id) : 'transparent', border: selected.includes(c.id) ? 'none' : '1px solid var(--border, #cbd5e1)' }" />
            <span>{{ c.label }}<em v-if="c.id === runId" class="muted small"> (this run)</em></span>
          </label>
          <label v-if="selected.includes(c.id)" class="mc-base" title="Measure the deltas against this run">
            <input type="radio" :checked="baseline === c.id" @change="baseline = c.id" /> baseline
          </label>
        </div>
      </div>
    </section>

    <div v-if="error" class="notice warn"><AlertTriangle :size="15" /><span>{{ error }}</span></div>
    <p v-if="selected.length < 2" class="muted center">Tick at least one more run to compare.</p>

    <template v-else-if="data">
      <section class="res-card">
        <div class="hm-title"><span>Metrics</span><span class="muted small">small figures: change against the baseline</span></div>
        <table class="data-table mc-table">
          <thead>
            <tr>
              <th>Run</th>
              <th v-for="col in columns" :key="col.key">{{ col.label }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in data.runs" :key="r.runId">
              <td>
                <i class="mc-dot" :style="{ background: colorOf(r.runId) }" />
                <strong>{{ r.design ? `${r.design} › ` : "" }}{{ r.variant ?? "Variant" }}</strong>
                <span class="muted small"> · {{ r.revision }} · {{ r.name }}</span>
                <span v-if="r.runId === baseline" class="chip led">baseline</span>
                <div v-if="r.state && (r.stateFallback || r.state.id !== 'all')" class="muted small">state: {{ r.state.name }}<template v-if="r.stateFallback"> (the requested state does not exist here)</template></div>
              </td>
              <template v-if="r.error">
                <td :colspan="columns.length" class="bad">{{ r.error }}</td>
              </template>
              <template v-else>
                <td v-for="col in columns" :key="col.key">
                  {{ cell(r, col) }}
                  <div v-if="r.runId !== baseline && base" class="small" :class="deltaClass(r, col)">{{ delta(r, col) }}</div>
                </td>
              </template>
            </tr>
          </tbody>
        </table>
      </section>

      <div class="res-grid">
        <section class="res-card">
          <div class="mc-axis">
            <span class="muted small">Axis along the length</span>
            <button class="btn small" :class="{ primary: axis === 'pct' }" @click="axis = 'pct'">% of length</button>
            <button class="btn small" :class="{ primary: axis === 'mm' }" @click="axis = 'mm'">mm</button>
          </div>
          <LineChart title="Profile along the length" :series="profileSeries" :x-label="axis === 'pct' ? '% of length' : 'mm'" :y-label="data.unit ?? ''" />
        </section>
        <section class="res-card">
          <LineChart title="Intensity by angle (relative to own peak)" :series="angleSeries" x-label="degrees" y-label="relative intensity" />
        </section>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { AlertTriangle } from "lucide-vue-next";
import { api, type Analysis, type ProjectCompare, type ProjectCompareRun } from "../api";
import { useWorkspace } from "../composables/useWorkspace";
import { fmtNum, fmtPct } from "../utils/colormap";
import LineChart from "./LineChart.vue";

export interface RunCandidate {
  id: string;
  label: string;
  group: string;
}

const props = defineProps<{ runId: string; state?: string; detector: number; analysis: Analysis; candidates: RunCandidate[] }>();

const ws = useWorkspace();
const PALETTE = ["#2563eb", "#ea580c", "#16a34a", "#9333ea", "#dc2626", "#0891b2", "#ca8a04", "#db2777", "#4b5563", "#65a30d", "#0d9488", "#7c3aed"];

const selected = ref<string[]>([props.runId]);
const baseline = ref(props.runId);
const axis = ref<"pct" | "mm">("pct");
const data = ref<ProjectCompare | null>(null);
const error = ref("");

const groups = computed(() => {
  const out: { name: string; items: RunCandidate[] }[] = [];
  for (const c of props.candidates) {
    let g = out.find((x) => x.name === c.group);
    if (!g) out.push((g = { name: c.group, items: [] }));
    g.items.push(c);
  }
  return out;
});

function toggle(id: string) {
  selected.value = selected.value.includes(id) ? selected.value.filter((x) => x !== id) : [...selected.value, id];
  if (!selected.value.includes(baseline.value)) baseline.value = selected.value[0] ?? "";
}
const colorOf = (id: string) => PALETTE[Math.max(0, selected.value.indexOf(id)) % PALETTE.length];

watch(
  () => [selected.value.join(","), props.state, props.detector, JSON.stringify(props.analysis)],
  async () => {
    data.value = null;
    if (selected.value.length < 2) return;
    try {
      data.value = await api.compareRuns(ws.project.value!.id, selected.value, { state: props.state, detector: props.detector, analysis: props.analysis });
      error.value = "";
    } catch (e) {
      error.value = (e as Error).message;
    }
  },
  { immediate: true },
);

// ── table ─────────────────────────────────────────────────────────────────────
interface Col {
  key: string;
  label: string;
  get: (r: ProjectCompareRun) => number | null | undefined;
  pct?: boolean;
  digits?: number;
  higherBetter: boolean | null;
}
const columns = computed<Col[]>(() => [
  { key: "eff", label: "Efficiency", get: (r) => r.efficiency, pct: true, higherBetter: true },
  { key: "exit", label: "Exit flux (lm)", get: (r) => r.exitLm, digits: 3, higherBetter: true },
  { key: "mean", label: `Average (${data.value?.unit ?? ""})`, get: (r) => r.stats?.mean, digits: 3, higherBetter: true },
  { key: "mm", label: "Min / max", get: (r) => r.stats?.minOverMax, pct: true, higherBetter: true },
  { key: "cv", label: "Variation (CV)", get: (r) => r.stats?.cv, pct: true, higherBetter: false },
  { key: "hot", label: "Hotspots", get: (r) => r.hotspots, digits: 0, higherBetter: false },
  { key: "cct", label: "CCT (K)", get: (r) => r.color?.cct, digits: 0, higherBetter: null },
  { key: "duv", label: "Max Δu′v′", get: (r) => r.color?.maxDuv, digits: 4, higherBetter: false },
]);
const base = computed(() => data.value?.runs.find((r) => r.runId === baseline.value && !r.error) ?? null);

function cell(r: ProjectCompareRun, c: Col): string {
  const v = c.get(r);
  if (v == null) return "–";
  return c.pct ? fmtPct(v) : fmtNum(v, c.digits ?? 3);
}
function diff(r: ProjectCompareRun, c: Col): number | null {
  const a = base.value ? c.get(base.value) : null;
  const b = c.get(r);
  return a == null || b == null ? null : b - a;
}
function delta(r: ProjectCompareRun, c: Col): string {
  const d = diff(r, c);
  if (d === null) return "";
  const sign = d >= 0 ? "+" : "";
  if (c.pct) return `${sign}${(d * 100).toFixed(1)} pts`;
  const a = base.value ? c.get(base.value) : null;
  const rel = a ? ` (${sign}${((d / Math.abs(a)) * 100).toFixed(1)} %)` : "";
  return `${sign}${d.toFixed(c.digits ?? 3)}${rel}`;
}
function deltaClass(r: ProjectCompareRun, c: Col): string {
  const d = diff(r, c);
  if (d === null || d === 0 || c.higherBetter === null) return "muted";
  return d > 0 === c.higherBetter ? "good" : "bad";
}

// ── charts ───────────────────────────────────────────────────────────────────
const ok = computed(() => (data.value?.runs ?? []).filter((r) => !r.error));
const lab = (r: ProjectCompareRun) => `${r.design ? `${r.design} › ` : ""}${r.variant ?? ""} · ${r.name ?? ""}`;
const profileSeries = computed(() =>
  ok.value.map((r) => {
    const p = (r.profileU ?? []) as (number | null)[];
    const len = axis.value === "mm" ? (r.uExtentMm ?? 0) : 100;
    return { label: lab(r), color: colorOf(r.runId), x: p.map((_, i) => (i / Math.max(p.length - 1, 1)) * len), y: p };
  }),
);
const angleSeries = computed(() => ok.value.map((r) => ({ label: lab(r), color: colorOf(r.runId), x: r.angular?.angles ?? [], y: (r.angular?.relative ?? []) as (number | null)[] })));
</script>

<style scoped>
.mc-group { margin-bottom: 8px; }
.mc-group-name { font-weight: 600; font-size: 12px; margin: 6px 0 2px; }
.mc-pick { display: flex; align-items: center; gap: 8px; padding: 2px 0; font-size: 13px; }
.mc-dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 6px; }
.mc-pick .mc-dot { margin-right: 0; }
.mc-base { margin-left: auto; display: inline-flex; gap: 4px; align-items: center; font-size: 12px; color: var(--muted, #64748b); }
.mc-table td { vertical-align: top; }
.mc-axis { display: flex; align-items: center; gap: 6px; margin-bottom: 4px; }
</style>
