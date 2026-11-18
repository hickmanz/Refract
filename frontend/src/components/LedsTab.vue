<template>
  <div class="tab-body">
    <p class="muted small">
      LEDs emit from a face of an LED part in your CAD (pick the emitting face), using a library model with its spectrum and angular pattern from the
      datasheet. Use <strong>Pick faces</strong> and click each LED once.
    </p>
    <CheckWarnings rematch-only />

    <div class="tab-actions">
      <PickButton :active="addActive" label="Pick faces to add LEDs" active-label="Picking LED faces: click to finish" @click="toggleAdd" />
      <label class="mini inline">
        <span>Model</span>
        <select v-model="ws.newLedModel.value">
          <option v-for="m in models" :key="m.id" :value="m.id">{{ m.name }}</option>
        </select>
      </label>
    </div>

    <ul class="led-list">
      <li v-for="led in leds" :key="led.id" class="led-item" :class="{ off: led.enabled === false }">
        <div class="led-head">
          <input :checked="led.enabled !== false" type="checkbox" title="Include this LED" @change="set(led, 'enabled', ($event.target as HTMLInputElement).checked)" />
          <input class="text-input name" :value="led.name" @change="set(led, 'name', ($event.target as HTMLInputElement).value)" />
          <button class="icon-btn" title="Duplicate" @click="duplicate(led)"><Copy :size="14" /></button>
          <button class="icon-btn" title="Remove" @click="remove(led)"><Trash2 :size="14" /></button>
        </div>
        <div class="led-fields">
          <label class="mini">
            <span>Model</span>
            <select :value="led.model" @change="set(led, 'model', ($event.target as HTMLSelectElement).value)">
              <option v-for="m in models" :key="m.id" :value="m.id">{{ m.name }}</option>
            </select>
          </label>
          <label class="mini narrow">
            <span>Scale</span>
            <input :value="led.scale ?? 1" type="number" min="0" max="10" step="0.05" @change="set(led, 'scale', Number(($event.target as HTMLInputElement).value))" />
          </label>
        </div>
        <div class="led-place">
          <span class="small" :class="placed(led) ? '' : 'error'">
            <template v-if="placed(led)"><Check :size="13" /> {{ placementText(led) }}</template>
            <template v-else>Not placed on a face yet</template>
          </span>
          <label class="check small"><input :checked="!!led.placement.flip" type="checkbox" @change="setFlip(led, ($event.target as HTMLInputElement).checked)" /> flip</label>
          <PickButton :active="isRepick(led)" label="Re-pick face" active-label="Click a face…" @click="repick(led)" />
        </div>
        <div v-if="modelOf(led)" class="muted small">
          {{ modelOf(led)!.channels.length }} channel{{ modelOf(led)!.channels.length === 1 ? "" : "s" }} ·
          {{ totalFlux(led).toFixed(1) }} lm
        </div>
      </li>
    </ul>
    <p v-if="!leds.length" class="muted center small">No LEDs yet.</p>

    <div v-if="stepPhases.length" class="tab-actions">
      <span class="muted small">{{ leds.length }} LED{{ leds.length === 1 ? "" : "s" }} · {{ totalAll.toFixed(1) }} lm total</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { Check, Copy, Trash2 } from "lucide-vue-next";
import type { LedDef } from "../api";
import { useWorkspace } from "../composables/useWorkspace";
import CheckWarnings from "./CheckWarnings.vue";
import PickButton from "./PickButton.vue";
import { newId } from "../utils/setup";

const ws = useWorkspace();

const leds = computed(() => ws.setup.value?.leds ?? []);
const models = computed(() => Object.values(ws.library.value?.leds ?? {}));
const modelOf = (led: LedDef) => ws.library.value?.leds[led.model];
const totalFlux = (led: LedDef) => (modelOf(led)?.channels ?? []).reduce((a, c) => a + c.flux_lm, 0) * (led.scale ?? 1);
const totalAll = computed(() => leds.value.filter((l) => l.enabled !== false).reduce((a, l) => a + totalFlux(l), 0));
const stepPhases = computed(() => leds.value);

const addActive = computed(() => ws.pick.value?.kind === "led" && ws.pick.value.ledId === "__new__");
function toggleAdd() {
  ws.pick.value = addActive.value ? null : { kind: "led", ledId: "__new__" };
}

const isRepick = (led: LedDef) => ws.pick.value?.kind === "led" && ws.pick.value.ledId === led.id;
function repick(led: LedDef) {
  ws.pick.value = isRepick(led) ? null : { kind: "led", ledId: led.id };
}
const placed = (led: LedDef) => (led.placement.type === "face" ? !!led.placement.part && led.placement.face != null : !!led.placement.origin);
function placementText(led: LedDef) {
  const p = led.placement;
  return p.type === "face" ? `${p.part} · face ${p.face}` : "free position";
}
function set<K extends keyof LedDef>(led: LedDef, key: K, value: LedDef[K]) {
  ws.mutate(() => {
    led[key] = value;
  });
}
function setFlip(led: LedDef, flip: boolean) {
  ws.mutate(() => {
    led.placement.flip = flip;
  });
}
function remove(led: LedDef) {
  ws.mutate((s) => {
    s.leds = s.leds.filter((l) => l.id !== led.id);
    for (const st of s.states) for (const k of Object.keys(st.drive)) if (k.startsWith(`${led.id}/`)) delete st.drive[k];
  });
}
function duplicate(led: LedDef) {
  ws.mutate((s) => {
    const id = newId("led", s.leds.map((l) => l.id));
    s.leds.push({ ...JSON.parse(JSON.stringify(led)), id, name: `${led.name} copy` });
  });
}
</script>
