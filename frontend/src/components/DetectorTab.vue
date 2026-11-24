<template>
  <div class="tab-body">
    <p class="muted small">
      The <strong>exit surface</strong> is where brightness, uniformity and colour are measured. Pick the face(s) of the light pipe that you look at. Rays leaving
      the part through these faces are counted (with the Fresnel loss at the surface); luminance is measured inside the viewing cone.
    </p>
    <p class="muted small">
      A <strong>receiver</strong> is for the light that lands on something, such as a table lit by the pipe. Pick the face(s) of that part (it can be opaque).
      The map shows the light that arrives (illuminance) and the light the surface sends towards a viewer (luminance). The viewer is where the blue arrow points.
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
        <div class="det-fields">
          <label class="mini wide" title="An exit surface counts the light leaving the light pipe through its faces. A receiver counts the light arriving on its faces (for example a table) and what they reflect towards a viewer.">
            <span>Type</span>
            <select :value="det.type ?? 'exit'" @change="setType(det, ($event.target as HTMLSelectElement).value as 'exit' | 'receiver')">
              <option value="exit">Exit surface (light leaving the pipe)</option>
              <option value="receiver">Receiver (light landing on a surface)</option>
            </select>
          </label>
          <label class="mini" :title="det.type === 'receiver' ? 'Half angle of the cone around the viewing direction in which the reflected light is measured' : undefined">
            <span>{{ det.type === 'receiver' ? 'View cone (half angle °)' : 'Viewing cone (half angle °)' }}</span>
            <input v-model.number="det.cone_deg" type="number" min="1" max="90" step="1" />
          </label>
          <label v-if="det.type !== 'receiver'" class="mini wide check-row" title="Normally the light that leaves through the exit surface is counted and ends there. Switch this on when it has to go on, for example to a table or a second detector. The numbers of this surface stay the same; the light that comes back and leaves again counts again.">
            <span><input v-model="det.pass_through" type="checkbox" /> Let the light go on after this surface</span>
          </label>
          <template v-if="det.type === 'receiver'">
            <label class="mini" title="Height of the viewer above the surface plane: 90 looks straight down at it, small angles look along it">
              <span>View elevation °</span>
              <input v-model.number="det.view_el" type="number" min="5" max="90" step="5" placeholder="90" />
            </label>
            <label class="mini" title="Direction of the viewer around the surface: 0 is along the red arrow of the map, 90 along the green one">
              <span>View azimuth °</span>
              <input v-model.number="det.view_az" type="number" min="-180" max="360" step="15" placeholder="0" />
            </label>
          </template>
          <label class="mini">
            <span>Resolution (px)</span>
            <select v-model.number="det.resolution">
              <option v-for="r in resolutions(det.resolution)" :key="r" :value="r">{{ r }}</option>
            </select>
          </label>
          <label class="mini wide" title="A flat map projects the surface along its average normal. Unwrapping flattens a curved surface so that every pixel covers the same area.">
            <span>Map of a curved surface</span>
            <select :value="det.mapping ?? 'auto'" @change="det.mapping = ($event.target as HTMLSelectElement).value as 'auto' | 'plane' | 'unwrap'">
              <option value="auto">Automatic (unwrap if curved)</option>
              <option value="plane">Flat projection</option>
              <option value="unwrap">Always unwrap</option>
            </select>
          </label>
        </div>
        <div class="tab-actions" title="The arrows in the 3D view show how the map is laid out: red points to the right of the map, green to the top.">
          <span class="small axis-key"><span class="axis-dot red" /> right <span class="axis-dot green" /> up</span>
          <button class="btn small" :class="{ on: det.flip_u }" @click="det.flip_u = !det.flip_u"><FlipHorizontal2 :size="14" /> Flip left–right</button>
          <button class="btn small" :class="{ on: det.flip_v }" @click="det.flip_v = !det.flip_v"><FlipVertical2 :size="14" /> Flip top–bottom</button>
        </div>
        <button
          v-if="det.faces.length"
          class="face-toggle"
          :aria-expanded="isOpen(det)"
          :title="isOpen(det) ? 'Hide the faces of this surface' : 'Show the faces of this surface'"
          @click="toggleOpen(det)"
        >
          <ChevronRight :size="14" class="chev" :class="{ open: isOpen(det) }" />
          <span class="small">{{ det.faces.length }} face{{ det.faces.length === 1 ? "" : "s" }}</span>
          <span v-if="!isOpen(det)" class="muted small summary">{{ summary(det) }}</span>
        </button>
        <ul v-if="det.faces.length && isOpen(det)" class="face-list">
          <li v-for="(f, i) in det.faces" :key="i">
            <span class="small">{{ f.part }} · face {{ f.face }}</span>
            <button class="icon-btn" title="Remove this face" @click="det.faces.splice(i, 1)"><X :size="13" /></button>
          </li>
        </ul>
        <p v-else-if="!det.faces.length" class="muted small">No faces yet.</p>
        <div class="tab-actions">
          <PickButton :active="picking(det.id)" label="Add faces in 3D" @click="togglePick(det.id)" />
          <span v-if="info(det.id)" class="muted small">
            {{ info(det.id)!.areaMm2.toFixed(1) }} mm² · map {{ info(det.id)!.nu }} × {{ info(det.id)!.nv }} px
          </span>
        </div>
      </li>
    </ul>
    <p v-if="!dets.length" class="muted center small">No exit surface yet.</p>
    <div class="tab-actions">
      <button class="btn small" @click="add('exit')"><Plus :size="14" /> Add exit surface</button>
      <button class="btn small" @click="add('receiver')"><Plus :size="14" /> Add receiver</button>
      <button v-if="dets.length > 1" class="btn small" :title="allOpen ? 'Hide the face lists of all surfaces' : 'Show the face lists of all surfaces'" @click="setAll(!allOpen)">
        <ChevronsUpDown :size="14" /> {{ allOpen ? "Collapse all" : "Expand all" }}
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive } from "vue";
import { ChevronRight, ChevronsUpDown, FlipHorizontal2, FlipVertical2, Info, Plus, Trash2, X } from "lucide-vue-next";
import { useWorkspace } from "../composables/useWorkspace";
import CheckWarnings from "./CheckWarnings.vue";
import PickButton from "./PickButton.vue";
import { newId } from "../utils/setup";
import type { DetectorDef } from "../api";

const ws = useWorkspace();
const dets = computed(() => ws.setup.value?.detectors ?? []);
const RESOLUTIONS = [80, 120, 200, 240, 300, 400, 600, 800, 1200];
/** The listed sizes, plus the one the detector has (a setup can hold a value that is not in the list). */
const resolutions = (cur: number) => (RESOLUTIONS.includes(cur) || !cur ? RESOLUTIONS : [...RESOLUTIONS, cur].sort((a, b) => a - b));
const info = (id: string) => ws.check.value?.detectors.find((d) => d.id === id);
const picking = (id: string) => ws.pick.value?.kind === "detector" && ws.pick.value.detectorId === id;
const togglePick = (id: string) => (ws.pick.value = picking(id) ? null : { kind: "detector", detectorId: id });

// the face list of an exit surface folds away; a short list (up to 3 faces) starts open, and the surface being picked into is always open
const open = reactive<Record<string, boolean>>({});
const isOpen = (d: DetectorDef) => picking(d.id) || (open[d.id] ?? d.faces.length <= 3);
/** One line for a folded list: the first face, and how many more. */
const summary = (d: DetectorDef) => {
  const f = d.faces[0];
  const first = `${f.part} · ${f.face}`;
  return d.faces.length > 1 ? `${first} +${d.faces.length - 1}` : first;
};
const toggleOpen = (d: DetectorDef) => (open[d.id] = !isOpen(d));
const allOpen = computed(() => dets.value.filter((d) => d.faces.length).every((d) => isOpen(d)));
const setAll = (on: boolean) => dets.value.forEach((d) => (open[d.id] = on));

function add(type: "exit" | "receiver" = "exit") {
  ws.mutate((s) => {
    const id = newId("det", s.detectors.map((d) => d.id));
    if (type === "receiver") s.detectors.push({ id, name: `Receiver ${s.detectors.length + 1}`, type, faces: [], cone_deg: 10, resolution: 240, view_el: 90, view_az: 0 });
    else s.detectors.push({ id, name: `Exit surface ${s.detectors.length + 1}`, faces: [], cone_deg: 30, resolution: 240 });
    ws.pick.value = { kind: "detector", detectorId: id };
  });
}
/** Switch a detector between exit surface and receiver (a receiver's viewing cone is narrower by default and it has a view direction). */
function setType(det: DetectorDef, type: "exit" | "receiver") {
  ws.mutate(() => {
    det.type = type;
    // a name still left at its default follows the type
    const n = /^(Exit surface|Receiver) (\d+)$/.exec(det.name);
    if (n) det.name = `${type === "receiver" ? "Receiver" : "Exit surface"} ${n[2]}`;
    if (type === "receiver") {
      det.view_el ??= 90;
      det.view_az ??= 0;
      if (det.cone_deg > 30) det.cone_deg = 10;
    }
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
.det-fields { display: grid; grid-template-columns: repeat(auto-fit, minmax(118px, 1fr)); gap: 8px; }
.det-fields .mini { min-width: 0; }
.det-fields .mini.wide { grid-column: 1 / -1; }
.det-fields .mini input, .det-fields .mini select { width: 100%; min-width: 0; box-sizing: border-box; }
.axis-key { white-space: nowrap; }
.check-row span { display: flex; align-items: center; gap: 6px; font-weight: 400; }
.check-row input { width: auto !important; }
.axis-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin: 0 2px 0 6px; vertical-align: baseline; }
.axis-dot.red { background: #ef4444; }
.axis-dot.green { background: #22c55e; }
</style>
