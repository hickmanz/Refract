<template>
  <div class="modal-backdrop" @click.self="$emit('close')">
    <div class="modal ov-modal">
      <div class="ov-head">
        <h3>Overview</h3>
        <span class="muted small">{{ projectName }} · every finished run, by design and variant</span>
        <span class="spacer" />
        <label v-if="states.length > 1" class="mini inline">
          <span>State</span>
          <select v-model="stateId">
            <option v-for="s in states" :key="s.id" :value="s.id">{{ s.name }}</option>
          </select>
        </label>
        <button class="icon-btn" title="Close" aria-label="Close" @click="$emit('close')"><X :size="18" /></button>
      </div>

      <div class="ov-body">
        <p v-if="loading" class="muted center"><Loader2 :size="16" class="spin" /> Loading…</p>
        <div v-else-if="error" class="notice warn"><AlertTriangle :size="15" /><span>{{ error }}</span></div>
        <p v-else-if="!rows.length" class="muted center">There is no finished run in this project yet.</p>

        <template v-else>
          <section class="ov-chart">
            <div class="hm-title">
              <span>Efficiency by variant</span>
              <span class="muted small">bar: best run · dots: the other runs</span>
            </div>
            <svg :viewBox="`0 0 ${chart.width} ${chart.height}`" class="ov-svg" role="img" aria-label="Efficiency by variant">
              <g v-for="t in chart.ticks" :key="t.v">
                <line :x1="chart.left" :x2="chart.width - 4" :y1="t.y" :y2="t.y" class="ov-grid" />
                <text :x="chart.left - 4" :y="t.y + 3" text-anchor="end" class="ov-tick">{{ t.label }}</text>
              </g>
              <g v-for="b in chart.bars" :key="b.id">
                <rect :x="b.x" :y="b.y" :width="b.w" :height="Math.max(0, chart.base - b.y)" class="ov-bar" :class="{ base: b.hasBaseline }">
                  <title>{{ b.title }}</title>
                </rect>
                <circle v-for="(d, i) in b.dots" :key="i" :cx="b.x + b.w / 2" :cy="d.y" r="3" class="ov-dot"><title>{{ d.title }}</title></circle>
                <text :x="b.x + b.w / 2" :y="chart.base + 12" text-anchor="middle" class="ov-tick">{{ b.label }}</text>
              </g>
              <text v-for="g in chart.groups" :key="g.id" :x="g.x" :y="chart.height - 3" text-anchor="middle" class="ov-design">{{ g.label }}</text>
            </svg>
          </section>

          <table class="data-table ov-table">
            <thead>
              <tr>
                <th>Run</th>
                <th v-for="c in columns" :key="c.key" :title="c.hint">{{ c.label }}</th>
              </tr>
            </thead>
            <tbody>
              <template v-for="d in groups" :key="d.id">
                <tr class="ov-design-row" @click="toggle('d:' + d.id)">
                  <td :colspan="columns.length + 1">
                    <component :is="collapsed.has('d:' + d.id) ? ChevronRight : ChevronDown" :size="14" />
                    <strong>{{ d.name }}</strong>
                    <span class="muted small"> · {{ d.count }} run{{ d.count === 1 ? "" : "s" }}</span>
                  </td>
                </tr>
                <template v-if="!collapsed.has('d:' + d.id)">
                  <template v-for="v in d.variants" :key="v.id">
                    <tr class="ov-variant-row" @click="toggle('v:' + v.id)">
                      <td>
                        <component :is="collapsed.has('v:' + v.id) ? ChevronRight : ChevronDown" :size="13" />
                        <strong>{{ v.name }}</strong>
                        <span class="muted small"> · {{ v.runs.length }} run{{ v.runs.length === 1 ? "" : "s" }}</span>
                      </td>
                      <td :colspan="columns.length" class="muted small ov-best">
                        <template v-if="v.best">best: {{ v.best.run.revision }} · {{ fmtPct(v.best.m?.efficiency) }} efficiency</template>
                      </td>
                    </tr>
                    <template v-if="!collapsed.has('v:' + v.id)">
                      <tr
                        v-for="r in v.runs"
                        :key="r.run.runId"
                        class="clickable ov-run-row"
                        :class="{ baseline: r.run.runId === baselineId }"
                        @click="$emit('open', r.run)"
                        @mouseenter="showBudget($event, r)"
                        @mousemove="moveBudget($event)"
                        @mouseleave="hover = null"
                      >
                        <td>
                          <label class="ov-base" title="Measure the other rows against this run" @click.stop>
                            <input type="radio" name="ov-baseline" :checked="r.run.runId === baselineId" @change="baselineId = r.run.runId" />
                          </label>
                          <span class="ov-run-name">{{ r.run.revision }} · {{ r.run.name }}</span>
                          <span v-if="r.run.runId === baselineId" class="chip led">baseline</span>
                          <span v-if="r.run.sweep" class="chip">sweep</span>
                          <span v-if="r.differs.length" class="chip warn" :title="'Not the same as the baseline: ' + r.differs.join(', ')">differs</span>
                          <div v-if="r.m && r.fallback" class="muted small">state: {{ r.m.name }} (the chosen state does not exist here)</div>
                        </td>
                        <template v-if="!r.m">
                          <td :colspan="columns.length" class="muted small">no results stored for this run</td>
                        </template>
                        <template v-else>
                          <td v-for="c in columns" :key="c.key">
                            {{ cell(r, c) }}
                            <div v-if="c.delta && r.run.runId !== baselineId && baseRow?.m" class="small" :class="deltaClass(r, c)">{{ delta(r, c) }}</div>
                          </td>
                        </template>
                      </tr>
                    </template>
                  </template>
                </template>
              </template>
            </tbody>
          </table>
          <p class="muted small ov-note">
            Figures are the ones stored with each run, using the analysis defaults it was saved with (not the live settings of the Compare tab). Differences smaller than
            the noise column are not real. Click a row to open its results.
          </p>
        </template>
      </div>

      <div v-if="hover" class="ov-pop" :style="{ left: hover.x + 'px', top: hover.y + 'px' }">
        <div class="ov-pop-title">Where the light went<span class="muted small"> · {{ hover.row.run.revision }} · {{ hover.row.run.name }}</span></div>
        <template v-if="hover.parts.length">
          <div class="ov-stack">
            <i v-for="p in hover.parts" :key="p.name" :style="{ width: p.fraction * 100 + '%', background: p.color }" :title="`${p.name}: ${(p.fraction * 100).toFixed(1)} %`" />
          </div>
          <ul class="ov-legend">
            <li v-for="p in hover.parts" :key="p.name">
              <i :style="{ background: p.color }" />
              <span>{{ p.name }}</span>
              <b>{{ (p.fraction * 100).toFixed(1) }} %</b>
            </li>
          </ul>
        </template>
        <p v-else class="muted small">This run has no energy budget stored.</p>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { AlertTriangle, ChevronDown, ChevronRight, Loader2, X } from "lucide-vue-next";
import { api, type OverviewRun, type ProjectOverview, type RunSummaryState } from "../api";
import { useWorkspace } from "../composables/useWorkspace";
import { fmtNum, fmtPct } from "../utils/colormap";

defineEmits<{ close: []; open: [run: OverviewRun] }>();

const ws = useWorkspace();
const projectName = computed(() => ws.project.value?.name ?? "");

const data = ref<ProjectOverview | null>(null);
const loading = ref(true);
const error = ref("");
const stateId = ref("all");
const baselineId = ref("");
const collapsed = ref(new Set<string>());
function toggle(key: string) {
  const next = new Set(collapsed.value);
  if (!next.delete(key)) next.add(key);
  collapsed.value = next;
}

onMounted(async () => {
  const pid = ws.project.value?.id;
  if (!pid) {
    error.value = "This revision does not belong to a project.";
    loading.value = false;
    return;
  }
  try {
    data.value = await api.projectOverview(pid);
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    loading.value = false;
  }
});

// ── states ───────────────────────────────────────────────────────────────────────
const states = computed(() => {
  const seen = new Map<string, string>();
  for (const r of data.value?.runs ?? []) for (const s of r.summary?.states ?? []) if (!seen.has(s.id)) seen.set(s.id, s.name);
  return [...seen].map(([id, name]) => ({ id, name }));
});
watch(
  states,
  (list) => {
    if (list.length && !list.some((s) => s.id === stateId.value)) stateId.value = list.find((s) => s.id === "all")?.id ?? list[0].id;
  },
  { immediate: true },
);

interface Metrics {
  name: string;
  efficiency: number;
  exitLm: number;
  mean: number | null;
  minOverMax: number | null;
  cv: number | null;
  hotspots: number;
  cct: number | null;
  loss: NonNullable<RunSummaryState["loss"]>;
}
interface Row {
  run: OverviewRun;
  m: Metrics | null;
  fallback: boolean;
  differs: string[];
}

function metricsOf(run: OverviewRun, want: string): { m: Metrics | null; fallback: boolean } {
  const list = run.summary?.states ?? [];
  const st = list.find((s) => s.id === want) ?? list.find((s) => s.id === "all") ?? list[0];
  const d = st?.detectors?.[0];
  if (!st || !d) return { m: null, fallback: false };
  return {
    fallback: st.id !== want,
    m: {
      name: st.name,
      efficiency: d.efficiency,
      exitLm: d.exitLm,
      mean: d.mean,
      minOverMax: d.minOverMax,
      cv: d.cv,
      hotspots: d.hotspots,
      cct: d.cct ?? null,
      loss: st.loss ?? [],
    },
  };
}

// every row (flat), then the grouping
const allRows = computed<Row[]>(() => (data.value?.runs ?? []).map((run) => ({ run, ...metricsOf(run, stateId.value), differs: [] })));
const baseRow = computed(() => allRows.value.find((r) => r.run.runId === baselineId.value) ?? null);

const rows = computed<Row[]>(() => {
  const b = baseRow.value;
  return allRows.value.map((r) => {
    const differs: string[] = [];
    if (b && r.run.runId !== b.run.runId) {
      if ((r.run.rays ?? null) !== (b.run.rays ?? null)) differs.push("ray count");
      if ((r.run.seed ?? null) !== (b.run.seed ?? null)) differs.push("seed");
      if ((r.run.mesh?.quality ?? "normal") !== (b.run.mesh?.quality ?? "normal")) differs.push("mesh quality");
      if (r.fallback !== b.fallback || (r.m && b.m && r.m.name !== b.m.name)) differs.push("state");
    }
    return { ...r, differs };
  });
});

// the baseline defaults to the newest run of the revision that is open, else the oldest run
watch(
  () => data.value,
  (d) => {
    if (!d || baselineId.value) return;
    const here = [...d.runs].reverse().find((r) => r.revisionId === ws.rid.value);
    baselineId.value = (here ?? d.runs[0])?.runId ?? "";
  },
);

interface VariantGroup {
  id: string;
  name: string;
  runs: Row[];
  best: Row | null;
}
interface DesignGroup {
  id: string;
  name: string;
  count: number;
  variants: VariantGroup[];
}
const groups = computed<DesignGroup[]>(() => {
  const d = data.value;
  if (!d) return [];
  const out: DesignGroup[] = [];
  for (const design of d.designs) {
    const variants: VariantGroup[] = [];
    for (const v of d.variants.filter((x) => x.designId === design.id)) {
      const runs = rows.value.filter((r) => r.run.variantId === v.id);
      if (!runs.length) continue;
      const withM = runs.filter((r) => r.m);
      const best = withM.length ? withM.reduce((a, b) => (b.m!.efficiency > a.m!.efficiency ? b : a)) : null;
      variants.push({ id: v.id, name: v.name, runs, best });
    }
    if (variants.length) out.push({ id: design.id, name: design.name, count: variants.reduce((n, v) => n + v.runs.length, 0), variants });
  }
  return out;
});

// ── columns, deltas ──────────────────────────────────────────────────────────────
interface Col {
  key: string;
  label: string;
  hint?: string;
  get: (r: Row) => number | null | undefined;
  pct?: boolean;
  digits?: number;
  higherBetter: boolean | null;
  delta: boolean;
  fmt?: (r: Row) => string;
}
const raysLabel = (r: Row) => {
  const n = r.run.raysTraced ?? r.run.rays;
  if (!n) return "–";
  return n >= 1e6 ? `${+(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${+(n / 1e3).toFixed(0)}k` : String(n);
};
const columns: Col[] = [
  { key: "eff", label: "Efficiency", hint: "Light at the exit surface as a share of the light the LEDs emit", get: (r) => r.m?.efficiency, pct: true, higherBetter: true, delta: true },
  { key: "mean", label: "Average", hint: "Mean over the exit surface", get: (r) => r.m?.mean, digits: 3, higherBetter: true, delta: true },
  { key: "mm", label: "Min / max", get: (r) => r.m?.minOverMax, pct: true, higherBetter: true, delta: true },
  { key: "cv", label: "Variation (CV)", get: (r) => r.m?.cv, pct: true, higherBetter: false, delta: true },
  { key: "hot", label: "Hotspots", get: (r) => r.m?.hotspots, digits: 0, higherBetter: false, delta: true },
  { key: "cct", label: "CCT (K)", get: (r) => r.m?.cct, digits: 0, higherBetter: null, delta: true },
  { key: "noise", label: "Noise", hint: "Median relative noise per map pixel: differences smaller than this are not real", get: (r) => r.run.mapNoise, pct: true, higherBetter: null, delta: false },
  { key: "rays", label: "Rays", hint: "Rays traced per channel", get: () => null, higherBetter: null, delta: false, fmt: raysLabel },
  {
    key: "mesh",
    label: "Mesh",
    hint: "Quality of the optical mesh the rays were traced against",
    get: () => null,
    higherBetter: null,
    delta: false,
    fmt: (r) => `${r.run.mesh?.quality ?? "normal"}${r.run.mesh?.triangles ? ` · ${Math.round(r.run.mesh.triangles / 1000)}k` : ""}`,
  },
];

function cell(r: Row, c: Col): string {
  if (c.fmt) return c.fmt(r);
  const v = c.get(r);
  if (v == null) return "–";
  return c.pct ? fmtPct(v) : fmtNum(v, c.digits ?? 3);
}
function diff(r: Row, c: Col): number | null {
  const a = baseRow.value ? c.get(baseRow.value) : null;
  const b = c.get(r);
  return a == null || b == null ? null : b - a;
}
function delta(r: Row, c: Col): string {
  const d = diff(r, c);
  if (d === null) return "";
  const sign = d >= 0 ? "+" : "";
  if (c.pct) return `${sign}${(d * 100).toFixed(1)} pts`;
  const a = baseRow.value ? c.get(baseRow.value) : null;
  const rel = a ? ` (${sign}${((d / Math.abs(a)) * 100).toFixed(1)} %)` : "";
  return `${sign}${d.toFixed(c.digits ?? 3)}${rel}`;
}
function deltaClass(r: Row, c: Col): string {
  const d = diff(r, c);
  if (d === null || d === 0 || c.higherBetter === null) return "muted";
  return d > 0 === c.higherBetter ? "good" : "bad";
}

// ── energy budget on hover ───────────────────────────────────────────────────────
const KIND_COLOR: Record<string, string> = { exit: "#16a34a", escaped: "#94a3b8", error: "#dc2626", noise: "#f59e0b" };
const ABSORB = ["#2563eb", "#ea580c", "#9333ea", "#0891b2", "#ca8a04", "#db2777", "#4b5563", "#65a30d"];
interface Part {
  name: string;
  fraction: number;
  color: string;
}
function budgetParts(m: Metrics | null): Part[] {
  if (!m || !m.loss.length) return [];
  const out: Part[] = [];
  let other = 0;
  let k = 0;
  for (const l of [...m.loss].sort((a, b) => (a.kind === "exit" ? -1 : b.kind === "exit" ? 1 : b.fraction - a.fraction))) {
    if (l.fraction <= 0) continue;
    if (l.kind === "absorbed") {
      if (l.fraction < 0.01) other += l.fraction;
      else out.push({ name: l.name, fraction: l.fraction, color: ABSORB[k++ % ABSORB.length] });
    } else out.push({ name: l.name, fraction: l.fraction, color: KIND_COLOR[l.kind] ?? "#64748b" });
  }
  if (other > 0) out.push({ name: "Other parts (each under 1 %)", fraction: other, color: "#cbd5e1" });
  return out;
}
const hover = ref<{ row: Row; parts: Part[]; x: number; y: number } | null>(null);
function place(e: MouseEvent) {
  return { x: Math.min(e.clientX + 16, window.innerWidth - 340), y: Math.min(e.clientY + 12, window.innerHeight - 260) };
}
function showBudget(e: MouseEvent, row: Row) {
  hover.value = { row, parts: budgetParts(row.m), ...place(e) };
}
function moveBudget(e: MouseEvent) {
  if (hover.value) hover.value = { ...hover.value, ...place(e) };
}

// ── chart: efficiency by variant ─────────────────────────────────────────────────
const chart = computed(() => {
  const left = 34;
  const top = 8;
  const base = 96;
  const height = 124;
  const bw = 34;
  const gap = 14;
  const dgap = 22;
  let x = left + 6;
  const bars: { id: string; x: number; y: number; w: number; label: string; title: string; hasBaseline: boolean; dots: { y: number; title: string }[] }[] = [];
  const grp: { id: string; label: string; x: number }[] = [];
  const effs = groups.value.flatMap((d) => d.variants.flatMap((v) => v.runs.map((r) => r.m?.efficiency ?? 0)));
  const max = Math.max(0.05, ...effs) * 1.15;
  const yOf = (e: number) => base - (e / max) * (base - top);
  for (const d of groups.value) {
    const x0 = x;
    for (const v of d.variants) {
      const ok = v.runs.filter((r) => r.m);
      const best = v.best;
      bars.push({
        id: v.id,
        x,
        w: bw,
        y: yOf(best?.m?.efficiency ?? 0),
        label: v.name.length > 8 ? v.name.slice(0, 7) + "…" : v.name,
        title: best ? `${d.name} › ${v.name}\nbest: ${best.run.revision} · ${best.run.name}: ${fmtPct(best.m!.efficiency)}` : v.name,
        hasBaseline: v.runs.some((r) => r.run.runId === baselineId.value),
        dots: ok.filter((r) => r !== best).map((r) => ({ y: yOf(r.m!.efficiency), title: `${r.run.revision} · ${r.run.name}: ${fmtPct(r.m!.efficiency)}` })),
      });
      x += bw + gap;
    }
    grp.push({ id: d.id, label: d.name.length > 18 ? d.name.slice(0, 17) + "…" : d.name, x: (x0 + x - gap) / 2 });
    x += dgap - gap;
  }
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => ({ v: f, y: yOf(max * f), label: `${(max * f * 100).toFixed(0)}%` }));
  return { left, base, height, width: Math.max(x + 4, 240), bars, groups: grp, ticks };
});
</script>

<style scoped>
.ov-modal {
  width: min(1280px, 96vw);
  height: min(860px, 92vh);
  display: flex;
  flex-direction: column;
  padding: 0;
}
.ov-head { display: flex; align-items: center; gap: 12px; padding: 10px 16px; border-bottom: 1px solid var(--neutral-6, #e2e8f0); }
.ov-head h3 { margin: 0; }
.ov-body { flex: 1; overflow: auto; padding: 12px 16px 16px; }
.ov-chart { margin-bottom: 12px; }
.ov-svg { width: 100%; max-width: 900px; height: auto; display: block; }
.ov-grid { stroke: var(--neutral-5, #e2e8f0); stroke-width: 1; }
.ov-tick { font-size: 9px; fill: var(--neutral-10, #64748b); }
.ov-design { font-size: 10px; font-weight: 600; fill: var(--neutral-12, #0f172a); }
.ov-bar { fill: #93c5fd; }
.ov-bar.base { fill: #2563eb; }
.ov-dot { fill: #1e3a8a; stroke: #fff; stroke-width: 1; }
.ov-table td { vertical-align: top; }
.ov-design-row td { background: var(--neutral-3, #f1f5f9); cursor: pointer; text-align: left !important; }
.ov-variant-row td { cursor: pointer; background: var(--neutral-2, #f8fafc); }
.ov-variant-row td:not(:first-child) { text-align: left; }
.ov-best { text-align: left !important; }
.ov-run-row td:first-child { padding-left: 28px; }
.ov-run-row.baseline { background: color-mix(in srgb, var(--radix-blue-3, #dbeafe) 55%, transparent); }
.ov-base { margin-right: 6px; }
.ov-run-name { margin-right: 6px; }
.ov-note { margin-top: 10px; }
.chip.warn { background: #fef3c7; color: #92400e; }
.ov-pop { position: fixed; z-index: 1000; width: 320px; background: var(--neutral-1, #fff); border: 1px solid var(--neutral-6, #cbd5e1); border-radius: 8px; box-shadow: 0 8px 24px rgba(0, 0, 0, 0.18); padding: 10px 12px; pointer-events: none; }
.ov-pop-title { font-weight: 600; font-size: 12px; margin-bottom: 6px; }
.ov-stack { display: flex; height: 12px; border-radius: 3px; overflow: hidden; margin-bottom: 8px; background: var(--neutral-4, #e2e8f0); }
.ov-stack i { display: block; height: 100%; }
.ov-legend { list-style: none; margin: 0; padding: 0; font-size: 12px; }
.ov-legend li { display: flex; align-items: center; gap: 6px; padding: 1px 0; }
.ov-legend li span { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ov-legend li i { width: 9px; height: 9px; border-radius: 2px; flex: none; }
.ov-legend b { font-variant-numeric: tabular-nums; }
</style>
