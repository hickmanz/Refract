<template>
  <div class="tab-body">
    <section class="run-box">
      <h4>Runs of this revision</h4>
      <p v-if="!plainRuns.length" class="muted small">No runs yet. Start one in the Run tab.</p>
      <button v-if="!plainRuns.length" class="btn small" @click="$emit('newRun')"><Play :size="14" /> Go to the Run tab</button>
      <ul class="run-list">
        <li v-for="r in plainRuns" :key="r.id" class="run-item" :class="{ selected: r.id === activeRun }" @click="r.status === 'ready' && $emit('open', r.id)">
          <div class="run-head">
            <span class="run-name">{{ r.name }}</span>
            <span class="chip" :class="r.status">{{ statusText[r.status] }}</span>
            <button v-if="r.status === 'queued' || r.status === 'running'" class="icon-btn" title="Cancel" @click.stop="cancel(r)"><X :size="14" /></button>
            <template v-else>
              <button class="icon-btn" title="Rename" @click.stop="rename(r)"><Pencil :size="13" /></button>
              <button class="icon-btn" title="Delete run" @click.stop="remove(r)"><Trash2 :size="14" /></button>
            </template>
          </div>
          <div v-if="r.status === 'running' || r.status === 'queued'" class="progress thin"><div class="progress-bar" :style="{ width: Math.round(r.progress.fraction * 100) + '%' }" /></div>
          <div v-if="r.status === 'running'" class="muted small">{{ r.progress.phase }}</div>
          <div v-else-if="r.status === 'failed'" class="error small">{{ r.error }}</div>
          <div v-else-if="r.status === 'ready' && r.summary?.states?.length" class="muted small">
            {{ fmtAgo(r.created) }} · <template v-if="r.solver === 'gpu'"><span :title="r.solverName">GPU</span> ·</template><template v-if="r.settings.one_channel_per_led">quick look (1 channel/LED) ·</template> {{ (r.raysTraced ?? r.settings.rays).toLocaleString() }} rays ·
            <template v-if="r.mapNoise != null">noise {{ (r.mapNoise * 100).toFixed(1) }} % ·</template>
            efficiency {{ fmtPct(r.summary.states[0].detectors[0]?.efficiency) }} ·
            {{ r.timing?.seconds }} s
          </div>
          <div v-else class="muted small">{{ fmtAgo(r.created) }}</div>
        </li>
      </ul>
    </section>

    <SweepPanel mode="results" @open="(id) => emit('open', id)" />
  </div>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { Pencil, Play, Trash2, X } from "lucide-vue-next";
import { api, type RunMeta } from "../api";
import { useWorkspace } from "../composables/useWorkspace";
import { fmtPct } from "../utils/colormap";
import { fmtAgo } from "../utils/format";
import SweepPanel from "./SweepPanel.vue";

defineProps<{ activeRun: string | null }>();
const emit = defineEmits<{ open: [id: string]; newRun: [] }>();

const ws = useWorkspace();
const statusText: Record<string, string> = { queued: "Queued", running: "Running", ready: "Done", failed: "Failed", cancelled: "Cancelled" };
const plainRuns = computed(() => ws.runs.value.filter((r) => !r.sweep));

async function cancel(r: RunMeta) {
  await api.cancelRun(r.id);
  await ws.loadRuns();
}
async function rename(r: RunMeta) {
  const n = window.prompt("Rename run", r.name);
  if (n && n.trim()) {
    await api.renameRun(r.id, n.trim());
    await ws.loadRuns();
  }
}
async function remove(r: RunMeta) {
  if (!confirm(`Delete run "${r.name}" and its results?`)) return;
  await api.deleteRun(r.id);
  await ws.loadRuns();
}
</script>
