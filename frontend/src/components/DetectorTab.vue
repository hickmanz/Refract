<template>
  <div class="tab-body">
    <p class="muted small">
      The <strong>exit surface</strong> is where brightness, uniformity and colour are measured. Pick the face(s) of the light pipe that you look at. Rays leaving
      the part through these faces are counted (with the Fresnel loss at the surface); luminance is measured inside the viewing cone.
    </p>
    <CheckWarnings rematch-only />
    <div v-if="ws.runOrientationStale.value" class="notice info">
      <Info :size="15" />
      <span>The opened run was made with the previous map orientation, so its results and the arrows on the model still show that one. Run again to see the flip.</span>
    </div>

    <ul class="det-list">
      <li v-for="det in dets" :key="det.id" class="det-item">
        <div class="led-head">
          <input v-model="det.name" class="text-input name" />
          <button class="icon-btn" title="Remove" @click="remove(det.id)"><Trash2 :size="14" /></button>
        </div>
        <div class="led-fields">
          <label class="mini narrow">
            <span>Viewing cone (half angle)</span>
            <input v-model.number="det.cone_deg" type="number" min="1" max="90" step="1" />
          </label>
          <label class="mini narrow">
            <span>Resolution (px, long side)</span>
            <select v-model.number="det.resolution">
              <option v-for="r in [80, 120, 200, 300, 400, 600, 800, 1200]" :key="r" :value="r">{{ r }}</option>
            </select>
          </label>
          <label class="mini" title="A flat map projects the surface along its average normal. Unwrapping flattens a curved surface so that every pixel covers the same area.">
            <span>Map of a curved surface</span>
            <select :value="det.mapping ?? 'auto'" @change="det.mapping = ($event.target as HTMLSelectElement).value as 'auto' | 'plane' | 'unwrap'">
              <option value="auto">Automatic (unwrap if curved)</option>
              <option value="plane">Flat projection</option>
              <option value="unwrap">Always unwrap</option>
            </select>
          </label>
        </div>
        <div class="tab-actions" title="The arrows in the 3D view show how the map is laid out: red points to the right of the map, green to the top.">
          <span class="small"><span class="axis-dot red" /> right <span class="axis-dot green" /> up</span>
          <button class="btn small" :class="{ on: det.flip_u }" @click="det.flip_u = !det.flip_u"><FlipHorizontal2 :size="14" /> Flip left–right</button>
          <button class="btn small" :class="{ on: det.flip_v }" @click="det.flip_v = !det.flip_v"><FlipVertical2 :size="14" /> Flip top–bottom</button>
        </div>
        <ul class="face-list">
          <li v-for="(f, i) in det.faces" :key="i">
            <span class="small">{{ f.part }} · face {{ f.face }}</span>
            <button class="icon-btn" title="Remove this face" @click="det.faces.splice(i, 1)"><X :size="13" /></button>
          </li>
        </ul>
        <div class="tab-actions">
          <PickButton :active="picking(det.id)" label="Add faces in 3D" @click="togglePick(det.id)" />
          <span v-if="info(det.id)" class="muted small">
            {{ info(det.id)!.areaMm2.toFixed(1) }} mm² · map {{ info(det.id)!.nu }} × {{ info(det.id)!.nv }} px
          </span>
        </div>
      </li>
    </ul>
    <p v-if="!dets.length" class="muted center small">No exit surface yet.</p>
    <div class="tab-actions"><button class="btn small" @click="add"><Plus :size="14" /> Add exit surface</button></div>
  </div>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { FlipHorizontal2, FlipVertical2, Info, Plus, Trash2, X } from "lucide-vue-next";
import { useWorkspace } from "../composables/useWorkspace";
import CheckWarnings from "./CheckWarnings.vue";
import PickButton from "./PickButton.vue";
import { newId } from "../utils/setup";

const ws = useWorkspace();
const dets = computed(() => ws.setup.value?.detectors ?? []);
const info = (id: string) => ws.check.value?.detectors.find((d) => d.id === id);
const picking = (id: string) => ws.pick.value?.kind === "detector" && ws.pick.value.detectorId === id;
const togglePick = (id: string) => (ws.pick.value = picking(id) ? null : { kind: "detector", detectorId: id });

function add() {
  ws.mutate((s) => {
    const id = newId("det", s.detectors.map((d) => d.id));
    s.detectors.push({ id, name: `Exit surface ${s.detectors.length + 1}`, faces: [], cone_deg: 30, resolution: 240 });
    ws.pick.value = { kind: "detector", detectorId: id };
  });
}
function remove(id: string) {
  ws.mutate((s) => {
    s.detectors = s.detectors.filter((d) => d.id !== id);
  });
  if (picking(id)) ws.pick.value = null;
}
</script>

<style scoped>
.axis-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin: 0 2px 0 6px; vertical-align: baseline; }
.axis-dot.red { background: #ef4444; }
.axis-dot.green { background: #22c55e; }
</style>
