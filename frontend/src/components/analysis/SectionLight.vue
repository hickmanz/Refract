<template>
  <div class="sl" @click.stop>
    <div class="sl-title">LIGHT AT THE CUT</div>
    <div v-if="!sl.axis.value" class="sl-hint">Turn on a cutting plane (X, Y or Z) above, then look at the light that passes through it.</div>
    <template v-else>
      <div v-if="sl.activeAxes.value.length > 1" class="sl-row">
        <span class="sl-lab">Plane</span>
        <button v-for="a in sl.activeAxes.value" :key="a" class="sl-chip" :class="{ on: sl.axis.value === a }" @click="sl.chosen.value = a">{{ a.toUpperCase() }}</button>
      </div>

      <div class="sl-row">
        <button class="sl-mode" :class="{ on: sl.mode.value === 'flux' }" title="Colour the cut by how much light passes through it" @click="sl.setMode('flux')">
          <span class="sl-mode-name">Light through the cut</span>
          <span class="sl-mode-sub">a heat map on the cut</span>
        </button>
        <button class="sl-mode" :class="{ on: sl.mode.value === 'fan' }" title="Show only the rays close to the cut, so the picture stays readable" @click="sl.setMode('fan')">
          <span class="sl-mode-name">Ray fan</span>
          <span class="sl-mode-sub">only the rays next to the cut</span>
        </button>
      </div>

      <template v-if="sl.mode.value === 'flux'">
        <div class="sl-row">
          <span class="sl-lab">Direction</span>
          <select v-model="sl.direction.value" class="sl-sel">
            <option value="both">Both ways</option>
            <option value="along">Towards +{{ axisName }}</option>
            <option value="against">Towards −{{ axisName }}</option>
          </select>
        </div>
        <div class="sl-row">
          <span class="sl-lab">Which light</span>
          <select v-model="sl.outcome.value" class="sl-sel">
            <option value="">All light</option>
            <option value="exit">Reaches the exit surface</option>
            <option value="absorbed">Gets absorbed</option>
            <option value="other">Goes elsewhere (lost / escapes)</option>
          </select>
        </div>
        <div v-if="sl.loading.value" class="sl-note"><span class="spinner small" /> Tracing the rays…</div>
        <div v-else-if="sl.error.value" class="sl-note err">{{ sl.error.value }}</div>
        <template v-else-if="sl.result.value">
          <div class="sl-bar">
            <span class="sl-bar-lab">0</span>
            <span class="sl-bar-grad" :style="{ background: grad }" />
            <span class="sl-bar-lab">{{ peak }}</span>
          </div>
          <div class="sl-note">% of the emitted light that passes per cm² of the cut. About {{ share }} % of all the light crosses this plane (a ray that crosses twice counts twice).</div>
          <div v-if="sl.result.value.crossings < 300" class="sl-note warn">Very few rays cross here, so the picture is noisy.</div>
        </template>
      </template>

      <template v-if="sl.mode.value === 'fan'">
        <div class="sl-row">
          <span class="sl-lab">Thickness</span>
          <input class="sl-range" type="range" :min="minT" :max="sl.maxThickness.value" :step="minT" :value="sl.effectiveThickness.value" @input="sl.thickness.value = +($event.target as HTMLInputElement).value" />
          <span class="sl-val">{{ sl.effectiveThickness.value.toFixed(1) }} mm</span>
        </div>
        <div class="sl-note">Shows the part of every ray that lies within this thickness of the cut, on the side that stays. Rays that run along the cut show up as full lines; rays that only cross it show as short ticks.</div>
        <div v-if="!av.rays.value && !av.rayLoading.value" class="sl-note">No rays yet. Show some from the results pane (a route, a region ...).</div>
        <div v-else-if="av.rayLoading.value" class="sl-note"><span class="spinner small" /> Tracing the rays…</div>
      </template>

      <button v-if="sl.mode.value !== 'off'" class="sl-look" title="Turn the camera to look straight at the cut" @click="sl.lookAtCut()">Look straight at the cut</button>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed } from "vue";
import type { AnalysisView } from "../../composables/useAnalysisView";
import type { SectionLight } from "../../composables/useSectionLight";
import { cssGradient, fmtNum } from "../../utils/colormap";

const props = defineProps<{ sl: SectionLight; av: AnalysisView }>();

const axisName = computed(() => (props.sl.axis.value ?? "x").toUpperCase());
const grad = cssGradient("turbo").replace("to top", "to right");
/** fraction of the emitted light per mm² → % per cm² */
const peak = computed(() => fmtNum(props.sl.vmax.value * 1e4, 2));
const share = computed(() => fmtNum((props.sl.result.value?.total ?? 0) * 100, 2));
const minT = computed(() => Math.max(0.05, Math.round(props.sl.maxThickness.value / 400 * 100) / 100));
</script>

<style scoped>
.sl { padding-top: 8px; display: flex; flex-direction: column; gap: 6px; }
.sl-title { font-size: 0.68rem; font-weight: 700; letter-spacing: 0.04em; text-transform: uppercase; color: var(--neutral-10); padding: 0 12px; }
.sl > :not(.sl-title) { margin: 0 12px; }
.sl-hint, .sl-note { font-size: 0.72rem; color: var(--neutral-10); line-height: 1.35; }
.sl-note.err { color: #dc2626; }
.sl-note.warn { color: #b45309; }
.sl-row { display: flex; align-items: center; gap: 6px; }
.sl-lab { font-size: 0.72rem; color: var(--neutral-10); min-width: 64px; }
.sl-sel { flex: 1; min-width: 0; font-size: 0.75rem; padding: 2px 4px; border: 1px solid var(--neutral-5); border-radius: 4px; background: var(--neutral-2); color: var(--neutral-12); }
.sl-chip { padding: 1px 8px; border: 1px solid var(--neutral-5); border-radius: 10px; background: var(--neutral-2); color: var(--neutral-10); font-size: 0.72rem; cursor: pointer; }
.sl-chip.on { background: var(--accent-8); border-color: var(--accent-8); color: #fff; }
.sl-mode { flex: 1; display: flex; flex-direction: column; align-items: flex-start; gap: 1px; padding: 5px 7px; border: 1px solid var(--neutral-5); border-radius: 6px; background: var(--neutral-2); color: var(--neutral-11); cursor: pointer; text-align: left; }
.sl-mode:hover { border-color: var(--neutral-7); }
.sl-mode.on { border-color: var(--accent-8); background: var(--accent-3, var(--neutral-3)); color: var(--neutral-12); box-shadow: inset 0 0 0 1px var(--accent-8); }
.sl-mode-name { font-size: 0.75rem; font-weight: 600; }
.sl-mode-sub { font-size: 0.65rem; color: var(--neutral-10); line-height: 1.2; }
.sl-bar { display: flex; align-items: center; gap: 6px; }
.sl-bar-grad { flex: 1; height: 10px; border-radius: 3px; border: 1px solid var(--neutral-5); }
.sl-bar-lab { font-size: 0.7rem; color: var(--neutral-10); font-variant-numeric: tabular-nums; }
.sl-range { flex: 1; min-width: 0; }
.sl-val { font-size: 0.72rem; color: var(--neutral-10); font-variant-numeric: tabular-nums; min-width: 48px; text-align: right; }
.sl-look { padding: 4px 8px; border: 1px solid var(--neutral-5); border-radius: 6px; background: var(--neutral-2); color: var(--neutral-11); font-size: 0.75rem; cursor: pointer; }
.sl-look:hover { border-color: var(--accent-8); }
</style>
