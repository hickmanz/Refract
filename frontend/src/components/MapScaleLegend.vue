<template>
  <div class="map-scale" role="img" aria-label="Colour scale of the map on the model">
    <div v-for="(s, i) in scales" :key="i" class="scale-row">
      <div class="scale-label" :title="s.label">{{ s.label }}</div>
      <div class="ramp">
        <span>0</span>
        <i class="grad" :style="{ background: gradient }" />
        <span>{{ fmtNum(s.vmax) }} {{ s.unit }}</span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import type { MapScale } from "../composables/useAnalysisView";
import { cssGradient, fmtNum } from "../utils/colormap";

defineProps<{ scales: MapScale[] }>();
const gradient = cssGradient("turbo").replace("to top", "to right");
</script>

<style scoped>
.map-scale {
  position: absolute;
  left: 12px;
  bottom: 12px;
  z-index: 20;
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 260px;
  max-width: calc(100% - 24px);
  padding: 8px 12px;
  border: 1px solid var(--neutral-4);
  border-radius: var(--radius-md);
  background: color-mix(in srgb, var(--neutral-1) 88%, transparent);
  font-size: 0.78rem;
  color: var(--neutral-11);
  pointer-events: none; /* never in the way of orbiting the model */
}
.scale-row { display: flex; flex-direction: column; gap: 3px; }
.scale-label { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ramp { display: flex; align-items: center; gap: 6px; font-size: 0.72rem; color: var(--neutral-10); font-variant-numeric: tabular-nums; }
.grad { flex: 1; height: 8px; border-radius: 4px; border: 1px solid var(--neutral-4); display: inline-block; }
</style>
