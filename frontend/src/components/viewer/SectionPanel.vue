<template>
  <Transition name="sp">
    <section v-if="visible" class="sp" @click.stop @pointerdown.stop @wheel.stop>
      <header class="sp-head">
        <Scissors :size="15" />
        <strong>Sections</strong>
        <span class="spacer" />
        <button class="sp-link" :disabled="activePlanes.size === 0" title="Remove every cut but keep this panel open" @click="emit('clear')">Clear cuts</button>
        <button class="icon-btn" title="Turn sections off and close this panel" aria-label="Turn sections off" @click="emit('close')"><X :size="14" /></button>
      </header>

      <div class="sp-planes">
        <div v-for="p in PLANES" :key="p.axis" class="sp-row" :class="{ on: activePlanes.has(p.axis) }">
          <button class="sp-axis" :class="{ on: activePlanes.has(p.axis) }" :title="`${p.name} plane: ${activePlanes.has(p.axis) ? 'switch off' : 'switch on'}`" @click="emit('toggle', p.axis)">
            <b>{{ p.axis.toUpperCase() }}</b><span>{{ p.name }}</span>
          </button>
          <input
            class="sp-dist"
            type="number"
            step="0.1"
            :disabled="!activePlanes.has(p.axis)"
            :value="planeDistances[p.axis].toFixed(3)"
            :title="`Where the ${p.name.toLowerCase()} plane cuts (you can also drag its handle)`"
            @change="onDistance(p.axis, $event)"
          />
          <button class="sp-flip" :class="{ on: flip[p.axis] }" :disabled="!activePlanes.has(p.axis)" title="Flip which side of the plane is removed" @click="emit('flip', p.axis, !flip[p.axis])">
            <ArrowLeftRight :size="13" /> Flip
          </button>
        </div>
      </div>

      <div class="sp-opts">
        <button class="sp-chip" :class="{ on: planeVisible }" :title="planeVisible ? 'Hide the plane handles (P)' : 'Show the plane handles (P)'" @click="emit('update:planeVisible', !planeVisible)">
          <component :is="planeVisible ? Eye : EyeOff" :size="13" /> Handles
        </button>
        <button class="sp-chip" :class="{ on: showCaps }" title="On: fill the cut ends. Off: leave the cut open to look into the parts" @click="emit('update:showCaps', !showCaps)">
          <Check v-if="showCaps" :size="13" /><span v-else class="sp-nocheck" /> Cap the cut
        </button>
      </div>

      <div v-if="$slots.default" class="sp-extra"><slot /></div>
    </section>
  </Transition>
</template>

<script setup lang="ts">
import { ArrowLeftRight, Check, Eye, EyeOff, Scissors, X } from "lucide-vue-next";

type Axis = "x" | "y" | "z";

defineProps<{
  visible: boolean;
  activePlanes: Set<Axis | "face">;
  flip: Record<Axis, boolean>;
  planeDistances: Record<Axis, number>;
  showCaps: boolean;
  planeVisible: boolean;
}>();

const emit = defineEmits<{
  (e: "close"): void;
  (e: "clear"): void;
  (e: "toggle", axis: Axis): void;
  (e: "flip", axis: Axis, value: boolean): void;
  (e: "setDistance", axis: Axis, distance: number): void;
  (e: "update:showCaps", value: boolean): void;
  (e: "update:planeVisible", value: boolean): void;
}>();

const PLANES: { axis: Axis; name: string }[] = [
  { axis: "x", name: "Right" },
  { axis: "y", name: "Top" },
  { axis: "z", name: "Front" },
];

function onDistance(axis: Axis, event: Event) {
  const val = parseFloat((event.target as HTMLInputElement).value);
  if (!Number.isNaN(val)) emit("setDistance", axis, val);
}
</script>

<style scoped>
.sp {
  width: 292px; max-height: calc(100vh - 220px); overflow-y: auto;
  display: flex; flex-direction: column; gap: 8px; padding: 10px 12px;
  border: 1px solid var(--neutral-4); border-radius: var(--radius-lg);
  background: color-mix(in srgb, var(--neutral-1) 95%, transparent);
  box-shadow: var(--shadow-md); color: var(--neutral-12);
  pointer-events: auto;
}
.sp-head { display: flex; align-items: center; gap: 6px; }
.sp-link {
  border: none; background: transparent; padding: 2px 4px; color: var(--accent-11); font: inherit; font-size: 0.76rem; font-weight: 600; cursor: pointer;
}
.sp-link:disabled { color: var(--neutral-9); cursor: default; }
.sp-link:hover:not(:disabled) { text-decoration: underline; }
.sp-planes { display: flex; flex-direction: column; gap: 6px; }
.sp-row { display: flex; align-items: center; gap: 6px; }
.sp-axis {
  flex: 1; min-width: 0; display: flex; align-items: center; gap: 8px; padding: 5px 8px;
  border: 1px solid var(--neutral-4); border-radius: var(--radius-md); background: var(--neutral-1);
  color: var(--neutral-11); font: inherit; font-size: 0.8rem; cursor: pointer; text-align: left;
}
.sp-axis b { font-size: 0.82rem; width: 12px; }
.sp-axis:hover { background: var(--neutral-3); }
.sp-axis.on { background: var(--accent-3); border-color: var(--accent-7); color: var(--neutral-12); }
.sp-dist {
  width: 74px; padding: 4px 6px; border: 1px solid var(--neutral-5); border-radius: var(--radius-md);
  background: var(--neutral-2); color: var(--neutral-12); font: inherit; font-size: 0.78rem; font-variant-numeric: tabular-nums; outline: none;
}
.sp-dist:focus { border-color: var(--accent-8); }
.sp-dist:disabled { opacity: 0.45; }
.sp-flip, .sp-chip {
  display: inline-flex; align-items: center; justify-content: center; gap: 4px; padding: 4px 8px;
  border: 1px solid var(--neutral-4); border-radius: var(--radius-md); background: var(--neutral-1);
  color: var(--neutral-11); font: inherit; font-size: 0.76rem; font-weight: 600; cursor: pointer; white-space: nowrap;
}
.sp-flip:hover:not(:disabled), .sp-chip:hover { background: var(--neutral-3); }
.sp-flip:disabled { opacity: 0.45; cursor: default; }
.sp-flip.on, .sp-chip.on { background: var(--accent-3); border-color: var(--accent-7); color: var(--neutral-12); }
.sp-opts { display: flex; gap: 6px; padding-top: 8px; border-top: 1px dashed var(--neutral-4); }
.sp-chip { flex: 1; }
.sp-nocheck { width: 13px; height: 13px; border: 1px solid var(--neutral-7); border-radius: 3px; display: inline-block; }
.sp-extra { padding-top: 2px; margin: 0 -12px -2px; border-top: 1px dashed var(--neutral-4); }
.sp-enter-active, .sp-leave-active { transition: opacity 0.15s ease, transform 0.15s ease; }
.sp-enter-from, .sp-leave-to { opacity: 0; transform: translateY(8px); }
</style>
