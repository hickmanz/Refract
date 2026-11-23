<template>
  <div class="tab-body">
    <p class="muted small">
      Give every part optical properties. The <strong>material</strong> is what the part is made of: a <em>volume</em> material makes it a body that light
      enters (clear or diffusing light pipe, lens, thin translucent white wall), an <em>opaque surface</em> material (PCB, plastic housing) only reflects
      or absorbs at the surface. The <strong>finish</strong> is how the surface is textured (polished, VDI, MT). With no material you can pick a
      ready-made surface preset instead. Click a part here or in the 3D view.
    </p>
    <CheckWarnings />

    <ul class="part-list">
      <li
        v-for="row in rows"
        :key="row.inst.id"
        class="part-item"
        :class="{ selected: ws.selectedPart.value === row.inst.id, off: !row.eff.enabled }"
        @click="ws.selectedPart.value = row.inst.id"
      >
        <div class="part-head">
          <button class="icon-btn" :title="row.eff.enabled ? 'Included in the simulation' : 'Ignored by the simulation'" @click.stop="toggle(row.inst)">
            <component :is="row.eff.enabled ? Eye : EyeOff" :size="15" />
          </button>
          <span class="part-name" :title="row.inst.path">{{ row.inst.name }}</span>
          <span v-if="row.isLed" class="chip led">LED</span>
          <span v-else-if="isVolume(row.eff.material)" class="chip glass">transparent</span>
          <span v-if="row.warn" class="warn-dot" :title="row.warn"><AlertTriangle :size="14" /></span>
          <button
            class="icon-btn mesh-btn"
            :class="{ on: meshOn(row.inst) }"
            :title="meshTitle(row.inst)"
            @click.stop="toggleMesh(row.inst)"
          >
            <Grid3x3 :size="14" />
          </button>
        </div>
        <div v-if="row.eff.enabled" class="part-fields" @click.stop>
          <label class="mini">
            <span>Material</span>
            <select :value="row.eff.material ?? ''" @change="setMaterial(row.inst, ($event.target as HTMLSelectElement).value)">
              <option value="">No material (use a surface preset)</option>
              <optgroup label="Opaque surface (PCB, plastic, paint)">
                <option v-for="m in surfaceMaterials" :key="m.id" :value="m.id">{{ m.name }}</option>
              </optgroup>
              <optgroup label="Volume (clear, diffusing, translucent)">
                <option v-for="m in volumeMaterials" :key="m.id" :value="m.id">{{ m.name }}</option>
              </optgroup>
            </select>
          </label>
          <label class="mini">
            <span>{{ row.eff.material ? "Texture / finish" : "Surface preset" }}</span>
            <select :value="row.eff.finish" @change="setFinish(row.inst, ($event.target as HTMLSelectElement).value)">
              <option v-for="f in finishFor(row.eff.material)" :key="f.id" :value="f.id">{{ f.name }}</option>
            </select>
          </label>
        </div>

        <div v-if="ws.selectedPart.value === row.inst.id && row.eff.enabled" class="overrides" @click.stop>
          <div class="overrides-head">
            <strong class="small">Face overrides</strong>
            <span class="muted small">different texture, surface preset or opaque material on single faces (painted end, mirror strip…)</span>
          </div>
          <div v-for="(fin, face) in row.eff.faces" :key="face" class="override-row">
            <button class="chip-btn" @click="focusFace(row.inst, Number(face))">Face {{ face }}</button>
            <select :value="fin" @change="setOverride(row.inst, Number(face), ($event.target as HTMLSelectElement).value)">
              <optgroup label="Textures">
                <option v-for="f in textureFinishes" :key="f.id" :value="f.id">{{ f.name }}</option>
              </optgroup>
              <optgroup label="Surface presets">
                <option v-for="f in presetFinishes" :key="f.id" :value="f.id">{{ f.name }}</option>
              </optgroup>
              <optgroup label="Opaque materials">
                <option v-for="m in surfaceMaterials" :key="m.id" :value="MAT_PREFIX + m.id">{{ m.name }}</option>
              </optgroup>
            </select>
            <button class="icon-btn" title="Remove" @click="removeOverride(row.inst, Number(face))"><X :size="14" /></button>
          </div>
          <div class="override-add">
            <select v-model="ws.overrideFinish.value">
              <optgroup label="Textures">
                <option v-for="f in textureFinishes" :key="f.id" :value="f.id">{{ f.name }}</option>
              </optgroup>
              <optgroup label="Surface presets">
                <option v-for="f in presetFinishes" :key="f.id" :value="f.id">{{ f.name }}</option>
              </optgroup>
              <optgroup label="Opaque materials">
                <option v-for="m in surfaceMaterials" :key="m.id" :value="MAT_PREFIX + m.id">{{ m.name }}</option>
              </optgroup>
            </select>
            <PickButton :active="pickActive(row.inst)" label="Pick faces in 3D" @click="startPick(row.inst)" />
          </div>
        </div>
      </li>
    </ul>

    <div class="tab-actions">
      <button class="btn small" @click="ws.mutate(resetAll)"><RotateCcw :size="14" /> Reset all parts</button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, watch } from "vue";
import { AlertTriangle, Eye, EyeOff, Grid3x3, RotateCcw, X } from "lucide-vue-next";
import type { FinishDoc, PartDoc, Setup } from "../api";
import { useWorkspace } from "../composables/useWorkspace";
import CheckWarnings from "./CheckWarnings.vue";
import PickButton from "./PickButton.vue";
import { instanceForPattern, isSurfaceMaterial, isVolumeMaterial, MAT_PREFIX, resolvePart, ruleFor } from "../utils/setup";

const ws = useWorkspace();

watch(ws.overrideFinish, (f) => {
  const p = ws.pick.value;
  if (p?.kind === "override") ws.pick.value = { ...p, finish: f };
});

const materials = computed(() => Object.values(ws.library.value?.materials ?? {}));
const surfaceMaterials = computed(() => materials.value.filter((m) => isSurfaceMaterial(m)));
const volumeMaterials = computed(() => materials.value.filter((m) => !isSurfaceMaterial(m)));
const allFinishes = computed(() => Object.values(ws.library.value?.finishes ?? {}));
/** Roughness-only finishes: polished, generic textures, VDI, MT. */
const textureFinishes = computed(() => allFinishes.value.filter(isTexture));
/** Bundled opaque surface presets (diffuse + gloss + roughness + colour in one entry). */
const presetFinishes = computed(() => allFinishes.value.filter((f) => f.type === "opaque"));
function isTexture(f: FinishDoc | undefined) {
  return f?.type === "dielectric" || f?.type === "texture";
}
function isVolume(material: string | null) {
  return isVolumeMaterial(ws.library.value, material);
}
function finishFor(material: string | null) {
  // a material (volume or opaque surface) is textured; without one the part takes a bundled surface preset
  return material ? textureFinishes.value : presetFinishes.value;
}

const ledParts = computed(() => {
  const ids = new Set<string>();
  for (const led of ws.setup.value?.leds ?? []) {
    if (led.placement.type === "face" && led.placement.part) for (const i of instanceForPattern(led.placement.part, ws.parts.value)) ids.add(i.id);
  }
  return ids;
});

const rows = computed(() => {
  const s = ws.setup.value;
  if (!s) return [];
  return ws.parts.value.map((inst) => {
    const eff = resolvePart(inst, s.parts);
    let warn = "";
    if (eff.enabled && isVolume(eff.material) && !inst.closed) warn = "The mesh is not watertight: this part will be treated as opaque.";
    else if (!inst.closed && eff.enabled) warn = "Open mesh (not watertight).";
    return { inst, eff, isLed: ledParts.value.has(inst.id), warn };
  });
});

/** Is this part's mesh drawn? With the toolbar "Mesh" toggle on, every part shown in the viewport counts. */
function meshOn(inst: PartDoc) {
  return ws.meshAll.value ? ws.shownParts.value.includes(inst.id) : ws.meshParts.value.includes(inst.id);
}
function toggleMesh(inst: PartDoc) {
  if (ws.meshAll.value) {
    // leave the global mode: keep every shown part's mesh except this one
    ws.meshParts.value = ws.shownParts.value.filter((i) => i !== inst.id);
    ws.meshAll.value = false;
    return;
  }
  const cur = ws.meshParts.value;
  ws.meshParts.value = cur.includes(inst.id) ? cur.filter((i) => i !== inst.id) : [...cur, inst.id];
}
function meshTitle(inst: PartDoc) {
  const shown = meshOn(inst);
  const tris = inst.triCount !== undefined ? `${inst.triCount.toLocaleString()} triangles` : "";
  const defl = inst.deflection !== undefined ? `, meshed to ${inst.deflection.toPrecision(2)} mm` : "";
  const what = shown ? "Hide the ray-traced mesh" : "Show the ray-traced mesh (colour: how far the facets depart from the smooth surface, grey = flat)";
  return tris ? `${what}\n${tris}${defl}` : what;
}
function edit(inst: PartDoc, fn: (rule: ReturnType<typeof ruleFor>) => void) {
  ws.mutate((s) => fn(ruleFor(s, inst, ws.parts.value)));
}
function toggle(inst: PartDoc) {
  const cur = resolvePart(inst, ws.setup.value?.parts ?? []).enabled;
  edit(inst, (r) => (r.enabled = !cur));
}
function setMaterial(inst: PartDoc, id: string) {
  edit(inst, (r) => {
    r.material = id || null;
    const fin = ws.library.value?.finishes[resolvePart(inst, ws.setup.value?.parts ?? []).finish];
    if (id && !isTexture(fin)) r.finish = isSurfaceMaterial(ws.library.value?.materials[id]) ? "as_material" : "polished";
    if (!id && isTexture(fin)) r.finish = "black";
  });
}
function setFinish(inst: PartDoc, id: string) {
  edit(inst, (r) => (r.finish = id));
}
function setOverride(inst: PartDoc, face: number, finish: string) {
  edit(inst, (r) => {
    r.faces = { ...(r.faces ?? {}), [String(face)]: finish };
  });
}
function removeOverride(inst: PartDoc, face: number) {
  edit(inst, (r) => {
    const faces = { ...(r.faces ?? {}) };
    delete faces[String(face)];
    r.faces = faces;
    if (r.face_sigs) delete r.face_sigs[String(face)];
  });
}
function pickActive(inst: PartDoc) {
  const p = ws.pick.value;
  return p?.kind === "override" && p.instanceId === inst.id;
}
function startPick(inst: PartDoc) {
  if (pickActive(inst)) ws.pick.value = null;
  else ws.pick.value = { kind: "override", instanceId: inst.id, finish: ws.overrideFinish.value };
}
function focusFace(inst: PartDoc, _face: number) {
  ws.selectedPart.value = inst.id;
}
function resetAll(s: Setup) {
  s.parts = [{ match: "*", finish: "black" }];
}

</script>
