<template>
  <div class="tab-body">
    <p class="muted small">
      The light analysis tells you what each <strong>surface</strong> does: how much of the exit light came off it, how much it absorbs, where its light goes.
      Define the surfaces here, for example one group per facet of the reflector. Faces of the light pipe itself (a prism, a texture) can be added too.
      Faces that are not in a group are not analysed. Changes apply to the next run.
    </p>
    <CheckWarnings rematch-only />
    <div v-if="ws.missingCount.value" class="notice warn">
      <AlertTriangle :size="15" />
      <div class="missing-banner">
        <span>
          {{ ws.missingCount.value }} face selection{{ ws.missingCount.value === 1 ? " is" : "s are" }} missing from the model, in
          {{ ws.groupsWithIssues.value.length }} group{{ ws.groupsWithIssues.value.length === 1 ? "" : "s" }}. They are marked below.
        </span>
        <button class="btn small" :disabled="ws.checking.value" @click="removeMissing"><Trash2 :size="14" /> Remove all missing faces from groups</button>
      </div>
    </div>
    <div class="notice info">
      <Info :size="15" />
      <span v-if="!groups.length">
        No groups defined: every <strong>opaque face</strong> (reflector, housing...) becomes a group on its own, the largest {{ MAX_AUTO }} first.
        <template v-if="autoCount"> Now {{ autoCount }} face groups.</template>
      </span>
      <span v-else>Only the groups below are analysed. Remove them all to go back to the automatic faces.</span>
    </div>

    <ul class="group-list">
      <li v-for="g in groups" :key="g.id" class="det-item" :class="{ focus: ws.focusGroup.value === g.id }" @mouseenter="ws.focusGroup.value = g.id" @mouseleave="ws.focusGroup.value = null">
        <div class="led-head">
          <input class="color-swatch" type="color" :value="colorOf(g)" title="Colour of this group" @input="g.color = ($event.target as HTMLInputElement).value" />
          <input v-model="g.name" class="text-input name" placeholder="Name of this surface" />
          <span v-if="issueCount(g)" class="issue-icon" :title="issueTitle(g)" role="img" :aria-label="issueTitle(g)"><AlertTriangle :size="15" /></span>
          <button class="icon-btn" title="Remove this group" @click="remove(g.id)"><Trash2 :size="14" /></button>
        </div>
        <button
          v-if="g.faces.length"
          class="face-toggle"
          :aria-expanded="isOpen(g)"
          :title="isOpen(g) ? 'Hide the faces of this group' : 'Show the faces of this group'"
          @click="toggleOpen(g)"
        >
          <ChevronRight :size="14" class="chev" :class="{ open: isOpen(g) }" />
          <span class="small">{{ g.faces.length }} face{{ g.faces.length === 1 ? "" : "s" }}</span>
          <span v-if="issueCount(g)" class="missing-count small"><AlertTriangle :size="12" /> {{ issueCount(g) }} missing</span>
          <span v-if="!isOpen(g)" class="muted small summary">{{ summary(g) }}</span>
        </button>
        <ul v-if="g.faces.length && isOpen(g)" class="face-list">
          <li v-for="(f, i) in g.faces" :key="i" :class="{ missing: ws.isMissing(g.id, f) }">
            <span class="small face-label">
              <AlertTriangle v-if="ws.isMissing(g.id, f)" :size="13" class="issue-icon" />
              {{ f.part }}{{ f.face === null ? " · whole part" : ` · face ${f.face}` }}
              <em v-if="ws.isMissing(g.id, f)">{{ missingWhy(g, f) }}</em>
            </span>
            <button class="icon-btn" title="Remove this face" @click="g.faces.splice(i, 1)"><X :size="13" /></button>
          </li>
        </ul>
        <p v-else-if="!g.faces.length" class="muted small">No faces yet.</p>
        <div class="tab-actions">
          <PickButton :active="picking(g.id)" label="Add faces in 3D" @click="togglePick(g.id)" />
          <span v-if="info(g.id)" class="muted small">{{ info(g.id)!.areaMm2.toFixed(1) }} mm²</span>
          <span v-else-if="g.faces.length && issueCount(g) < g.faces.length" class="muted small">checking…</span>
          <span v-else-if="g.faces.length" class="muted small">no faces left in this model</span>
        </div>
      </li>
    </ul>

    <div class="tab-actions">
      <button class="btn small" @click="add"><Plus :size="14" /> Add group</button>
      <button v-if="groups.length > 1" class="btn small" :title="allOpen ? 'Hide the face lists of all groups' : 'Show the face lists of all groups'" @click="setAll(!allOpen)">
        <ChevronsUpDown :size="14" /> {{ allOpen ? "Collapse all" : "Expand all" }}
      </button>
      <button class="btn small" :disabled="!selectedPart" :title="selectedPart ? '' : 'Select a part first (Parts tab or 3D view)'" @click="perFace">
        <Layers :size="14" /> One group per face of {{ selectedPart ? selectedPart.name : "the selected part" }}
      </button>
    </div>
    <p v-if="message" class="muted small">{{ message }}</p>
  </div>
</template>

<script setup lang="ts">
import { computed, onActivated, onBeforeUnmount, onDeactivated, onMounted, reactive, ref } from "vue";
import { AlertTriangle, ChevronRight, ChevronsUpDown, Info, Layers, Plus, Trash2, X } from "lucide-vue-next";
import { api, type AnalysisGroupDef } from "../api";
import { useWorkspace } from "../composables/useWorkspace";
import CheckWarnings from "./CheckWarnings.vue";
import PickButton from "./PickButton.vue";
import { newId, partPattern, sigOf } from "../utils/setup";

const MAX_AUTO = 62;
const PALETTE = ["#3b82f6", "#ef4444", "#22c55e", "#f59e0b", "#a855f7", "#14b8a6", "#ec4899", "#84cc16", "#06b6d4", "#f97316"];

const ws = useWorkspace();
const groups = computed<AnalysisGroupDef[]>(() => ws.setup.value?.analysisGroups ?? []);
const info = (id: string) => ws.check.value?.groups?.find((g) => g.id === id);
const autoCount = computed(() => (ws.check.value?.groups ?? []).filter((g) => g.auto && g.index < 62).length);
const selectedPart = computed(() => ws.parts.value.find((p) => p.id === ws.selectedPart.value) ?? null);
const message = ref("");

// the face list of a group folds away; a short list (up to 3 faces) starts open, and the group being picked into is always open
const open = reactive<Record<string, boolean>>({});
const isOpen = (g: AnalysisGroupDef) => picking(g.id) || (open[g.id] ?? g.faces.length <= 3);
/** One line for a folded list: the first face, and how many more. */
const summary = (g: AnalysisGroupDef) => {
  const f = g.faces[0];
  const first = `${f.part}${f.face === null ? "" : ` · ${f.face}`}`;
  return g.faces.length > 1 ? `${first} +${g.faces.length - 1}` : first;
};
const toggleOpen = (g: AnalysisGroupDef) => (open[g.id] = !isOpen(g));
const allOpen = computed(() => groups.value.filter((g) => g.faces.length).every((g) => isOpen(g)));
const setAll = (on: boolean) => groups.value.forEach((g) => (open[g.id] = on));

// faces that a group points to but the model does not have (typically after importing a new STEP variant)
const issueCount = (g: AnalysisGroupDef) => ws.issuesFor(g.id).length;
const issueTitle = (g: AnalysisGroupDef) => {
  const n = issueCount(g);
  return `${n} face${n === 1 ? "" : "s"} of this group ${n === 1 ? "is" : "are"} missing from the model`;
};
const missingWhy = (g: AnalysisGroupDef, f: AnalysisGroupDef["faces"][number]) =>
  ws.issuesFor(g.id).find((m) => m.part === f.part && (m.face ?? null) === (f.face ?? null))?.reason === "part" ? "part is missing" : "missing";
function removeMissing() {
  const n = ws.removeMissingFaces();
  message.value = n ? `Removed ${n} missing face selection${n === 1 ? "" : "s"} from the groups.` : "";
}

const colorOf = (g: AnalysisGroupDef) => g.color || info(g.id)?.color || "#8b5cf6";
const picking = (id: string) => ws.pick.value?.kind === "group" && ws.pick.value.groupId === id;
const togglePick = (id: string) => (ws.pick.value = picking(id) ? null : { kind: "group", groupId: id });

// the faces of the groups are painted on the model while this tab is open
const show = (on: boolean) => {
  ws.showGroups.value = on;
  if (!on) ws.focusGroup.value = null;
};
onMounted(() => show(true));
onActivated(() => show(true));
onDeactivated(() => show(false));
onBeforeUnmount(() => show(false));

function add() {
  ws.mutate((s) => {
    const list = (s.analysisGroups ??= []);
    const id = newId("grp", list.map((g) => g.id));
    list.push({ id, name: `Surface ${list.length + 1}`, color: PALETTE[list.length % PALETTE.length], faces: [] });
    ws.pick.value = { kind: "group", groupId: id };
  });
}
function remove(id: string) {
  ws.mutate((s) => {
    s.analysisGroups = (s.analysisGroups ?? []).filter((g) => g.id !== id);
  });
  if (picking(id)) ws.pick.value = null;
}

/** A group for every face of the selected part (the part's faces, at most 62 groups in total). */
async function perFace() {
  const inst = selectedPart.value;
  if (!inst) return;
  const room = MAX_AUTO - groups.value.length;
  const count = Math.min(inst.faceCount, room);
  if (count <= 0) {
    message.value = "There is no room for more groups (the limit is 62).";
    return;
  }
  const pattern = partPattern(inst, ws.parts.value);
  const sigs = await Promise.all(
    Array.from({ length: count }, (_, f) =>
      api
        .face(ws.rid.value, inst.id, f)
        .then(sigOf)
        .catch(() => null),
    ),
  );
  ws.mutate((s) => {
    const list = (s.analysisGroups ??= []);
    for (let f = 0; f < count; f++) {
      const id = newId("grp", list.map((g) => g.id));
      list.push({ id, name: `${inst.name} / face ${f}`, color: PALETTE[list.length % PALETTE.length], faces: [{ part: pattern, face: f, sig: sigs[f] }] });
    }
  });
  message.value = inst.faceCount > count ? `${inst.name} has ${inst.faceCount} faces; the first ${count} became groups.` : "";
}
</script>

<style scoped>
.group-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.det-item.focus { border-color: var(--accent-8); }
.missing-banner { display: flex; flex-direction: column; gap: 6px; align-items: flex-start; min-width: 0; }
.issue-icon { display: inline-flex; align-items: center; flex: none; color: var(--radix-yellow-9, #ca8a04); }
.dark .issue-icon { color: var(--radix-yellow-11, #facc15); }
.missing-count { display: inline-flex; align-items: center; gap: 3px; color: var(--radix-yellow-9, #ca8a04); }
.dark .missing-count { color: var(--radix-yellow-11, #facc15); }
.face-list li.missing { background: var(--radix-yellow-2, var(--neutral-2)); }
.face-label { display: inline-flex; align-items: center; gap: 5px; min-width: 0; }
.face-list li.missing .face-label { text-decoration: line-through; text-decoration-color: var(--neutral-8); }
.face-list li.missing .face-label em { font-style: normal; text-decoration: none; display: inline-block; color: var(--radix-yellow-9, #ca8a04); }
.color-swatch { width: 28px; height: 24px; padding: 0; border: 1px solid var(--neutral-4); border-radius: 4px; background: none; flex: none; }
</style>
