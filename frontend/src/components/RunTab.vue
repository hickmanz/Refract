<template>
  <div class="tab-body">
    <div v-if="active.length" class="notice info">
      <Loader2 :size="15" class="spin" />
      <div class="active-run">
        <span>
          <template v-if="active.length === 1">
            <strong>{{ active[0].name }}</strong> is {{ active[0].status === "queued" ? "queued" : "running" }}, {{ Math.round(active[0].progress.fraction * 100) }} %.
          </template>
          <template v-else>{{ active.length }} runs are in progress, {{ Math.round(overall * 100) }} % overall.</template>
        </span>
        <button class="btn small" @click="emit('showResults')"><BarChart3 :size="14" /> See progress in Results</button>
      </div>
    </div>

    <section class="run-box">
      <h4>Simulate this revision</h4>
      <div v-if="ws.checkError.value" class="notice warn"><AlertTriangle :size="15" /><span>{{ ws.checkError.value }}</span></div>
      <template v-else-if="ws.check.value">
        <p class="small muted">
          {{ ws.check.value.channels.length }} source channel{{ ws.check.value.channels.length === 1 ? "" : "s" }} ·
          {{ ws.check.value.detectors.length }} exit surface{{ ws.check.value.detectors.length === 1 ? "" : "s" }} ·
          {{ ws.check.value.triangles.toLocaleString() }} triangles
        </p>
        <CheckWarnings />
      </template>
      <p v-else class="muted small">Checking the setup…</p>

      <label v-if="gpu?.available" class="mini" title="The GPU traces the same physics about 20 times faster, with the light path analysis switched on (about 10 times faster than the CPU with it) on every ray and every pass, also when the run stops early at a noise target.">
        <span>Solver</span>
        <select v-model="chosen">
          <option value="cpu">CPU (Embree + Numba)</option>
          <option value="gpu">GPU ({{ gpu.name }})</option>
        </select>
      </label>
      <p v-else-if="gpu" class="muted small" :title="gpu.reason ?? ''">GPU solver not available on this machine.</p>
      <label class="mini">
        <span>Rays per source channel</span>
        <select v-model.number="rays">
          <option v-for="r in rayOptions" :key="r" :value="r">{{ rayLabel(r) }}</option>
        </select>
      </label>
      <p class="muted small">
        About {{ estimate }}{{ targetNoise > 0 ? " at most" : "" }} for {{ channelCount }} channel{{ channelCount === 1 ? "" : "s" }}. Use more rays for smooth colour maps and hotspot counts; 5 to 10 million per channel is typical for a map you can judge shading on.
      </p>
      <label class="mini" title="Trace in growing passes and stop as soon as the exit map is this quiet. The number of rays above is then the most it will use.">
        <span>Stop early when map noise is below</span>
        <select v-model.number="targetNoise">
          <option :value="0">Off (trace exactly this many rays)</option>
          <option :value="0.1">10 %</option>
          <option :value="0.05">5 % (converged)</option>
          <option :value="0.03">3 %</option>
        </select>
      </label>
      <label v-if="targetNoise > 0 && solver !== 'gpu'" class="check small" title="After the map has converged, the first 2 million rays of every channel are traced once more with the light path analysis switched on. That adds a minute or two; the analysis views are then based on those rays (the ray lists are the same as in any run, the surface shares and deficit map are a little noisier than the main map).">
        <input v-model="keepAnalysis" type="checkbox" /> Keep light path analysis (adds a final pass of about 2 million rays per channel)
      </label>
      <label class="check small" title="Traces only the brightest driven channel of every LED and leaves the others out. About as many times faster as an LED has channels. The result shows how that channel's light is spread, so use it to judge dispersion, not colour or absolute luminance.">
        <input v-model="oneChannel" type="checkbox" /> Quick look: one channel per LED (judges light spread only, not colour or brightness)
      </label>
      <label class="mini">
        <span>Name (optional)</span>
        <input v-model="name" class="text-input" placeholder="e.g. 0.05 % TiO2, texture fine" />
      </label>
      <button class="btn primary" :disabled="!ws.check.value || busy" @click="start"><Play :size="15" /> Run simulation</button>
      <p v-if="error" class="error small">{{ error }}</p>
    </section>

    <SweepPanel mode="form" @started="emit('sweepStarted')" />
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";
import { AlertTriangle, BarChart3, Loader2, Play } from "lucide-vue-next";
import { api } from "../api";
import { useWorkspace } from "../composables/useWorkspace";
import CheckWarnings from "./CheckWarnings.vue";
import SweepPanel from "./SweepPanel.vue";
import { useSolvers } from "../composables/useSolvers";

const emit = defineEmits<{ started: [id: string]; sweepStarted: []; showResults: [] }>();

const ws = useWorkspace();
/** Runs (and the runs of a sweep) that are queued or running right now. */
const active = computed(() => ws.runs.value.filter((r) => r.status === "queued" || r.status === "running"));
const overall = computed(() => active.value.reduce((s, r) => s + r.progress.fraction, 0) / Math.max(active.value.length, 1));
const { gpu, chosen, solver } = useSolvers();
const rays = ref(200000);
const rayOptions = [50000, 200000, 500000, 1000000, 2000000, 5000000, 10000000, 20000000, 50000000];
const rayNotes: Record<number, string> = { 200000: "quick look", 2000000: "smooth", 10000000: "converged map", 50000000: "maximum" };
function rayLabel(r: number): string {
  return r.toLocaleString() + (rayNotes[r] ? ` (${rayNotes[r]})` : "");
}
const targetNoise = ref(0);
const keepAnalysis = ref(false); // only asked for when the run can stop early; an ordinary run always keeps it
const oneChannel = ref(false);
const name = ref("");
const busy = ref(false);
const error = ref("");
const channelCount = computed(() => {
  const ch = ws.check.value?.channels ?? [];
  return oneChannel.value ? new Set(ch.map((c) => c.led)).size : ch.length; // upper bound: channels that no state drives are skipped anyway
});
const estimate = computed(() => {
  const s = (rays.value * Math.max(channelCount.value, 1)) / (solver.value === "gpu" ? 1500000 : 140000);
  return s < 90 ? `${Math.max(1, Math.round(s))} s` : `${(s / 60).toFixed(1)} min`;
});

async function start() {
  busy.value = true;
  error.value = "";
  try {
    await ws.saveNow();
    const run = await api.createRun(ws.rid.value, { name: name.value, settings: { rays: rays.value, solver: solver.value, target_noise: targetNoise.value, one_channel_per_led: oneChannel.value, ...(targetNoise.value > 0 && solver.value !== "gpu" ? { analysis: keepAnalysis.value } : {}) } });
    name.value = "";
    await ws.loadRuns();
    emit("started", run.id);
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    busy.value = false;
  }
}
</script>

<style scoped>
.active-run { display: flex; flex-direction: column; gap: 6px; align-items: flex-start; min-width: 0; }
.spin { animation: spin 1s linear infinite; }
</style>
