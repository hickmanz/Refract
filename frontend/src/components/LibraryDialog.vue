<template>
  <div class="modal-backdrop" @click.self="$emit('close')">
    <div class="modal library-modal">
      <div class="lib-head">
        <h3>Library</h3>
        <div class="seg">
          <button v-for="k in kinds" :key="k.id" :class="{ on: kind === k.id }" @click="setKind(k.id)">{{ k.label }}</button>
        </div>
        <span class="spacer" />
        <button class="icon-btn" title="Close" @click="$emit('close')"><X :size="18" /></button>
      </div>

      <div class="lib-body">
        <aside class="lib-list">
          <button class="btn small" @click="newEntry"><Plus :size="14" /> New</button>
          <ul>
            <li v-for="e in entries" :key="e.id" :class="{ selected: e.id === selectedId }" @click="select(e.id)">
              <span class="lib-name">{{ e.name }}</span>
              <span v-if="kindChip(e)" class="chip">{{ kindChip(e) }}</span>
              <span v-if="e.builtin" class="chip">built-in</span>
            </li>
          </ul>
        </aside>

        <section v-if="draft" class="lib-edit">
          <div class="lib-tools">
            <label class="check small"><input v-model="jsonMode" type="checkbox" /> Edit as JSON</label>
            <span class="spacer" />
            <button v-if="draft.builtin" class="btn small" @click="duplicate"><Copy :size="14" /> Duplicate to edit</button>
            <button v-else class="btn small" :disabled="!selectedId" @click="remove"><Trash2 :size="14" /> Delete</button>
            <button class="btn primary small" :disabled="!!draft.builtin || saving" @click="save"><Save :size="14" /> Save</button>
          </div>
          <p v-if="message" class="small" :class="messageOk ? 'ok-text' : 'error'">{{ message }}</p>

          <template v-if="jsonMode">
            <textarea v-model="jsonText" class="json-area" spellcheck="false" @input="jsonDirty = true" />
            <button class="btn small" @click="applyJson">Apply JSON</button>
          </template>
          <template v-else>
            <MaterialForm v-if="kind === 'materials'" :doc="draft as MaterialDoc" :readonly="!!draft.builtin" />
            <FinishForm v-else-if="kind === 'finishes'" :doc="draft as FinishDoc" :readonly="!!draft.builtin" />
            <LedForm v-else :doc="draft as LedModel" :readonly="!!draft.builtin" />
          </template>
        </section>
        <section v-else class="lib-edit muted center">Select an entry or create a new one.</section>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { Copy, Plus, Save, Trash2, X } from "lucide-vue-next";
import { api, type FinishDoc, type LedModel, type LibraryKind, type MaterialDoc } from "../api";
import { useWorkspace } from "../composables/useWorkspace";
import MaterialForm from "./library/MaterialForm.vue";
import FinishForm from "./library/FinishForm.vue";
import LedForm from "./library/LedForm.vue";

defineEmits<{ close: [] }>();
const ws = useWorkspace();

const kinds: { id: LibraryKind; label: string }[] = [
  { id: "materials", label: "Materials" },
  { id: "finishes", label: "Textures & finishes" },
  { id: "leds", label: "LEDs" },
];
const kind = ref<LibraryKind>("materials");
const selectedId = ref<string | null>(null);
type AnyDoc = (MaterialDoc | FinishDoc | LedModel) & { builtin?: boolean };
const draft = ref<AnyDoc | null>(null);
const jsonMode = ref(false);
const jsonText = ref("");
const jsonDirty = ref(false);
const message = ref("");
const messageOk = ref(false);
const saving = ref(false);

const entries = computed(() => Object.values(ws.library.value?.[kind.value] ?? {}).sort((a, b) => Number(!!b.builtin) - Number(!!a.builtin) || a.name.localeCompare(b.name)));

/** Short label that tells the roles apart in the list: opaque surface vs volume material, surface preset vs texture. */
function kindChip(e: AnyDoc): string {
  if (kind.value === "materials") return (e as MaterialDoc).mode === "surface" ? "opaque surface" : "volume";
  if (kind.value === "finishes") return (e as FinishDoc).type === "opaque" ? "surface preset" : "texture";
  return "";
}
function clone<T>(x: T): T {
  return JSON.parse(JSON.stringify(x));
}
function setKind(k: LibraryKind) {
  kind.value = k;
  selectedId.value = null;
  draft.value = null;
  message.value = "";
}
function select(id: string) {
  selectedId.value = id;
  draft.value = clone((ws.library.value as NonNullable<typeof ws.library.value>)[kind.value][id]) as AnyDoc;
  message.value = "";
}
function template(): AnyDoc {
  if (kind.value === "materials") return { id: "", name: "New material", n: { type: "cauchy", A: 1.58, B: 0.01 }, absorption: 0.001, scattering: { type: "none" } } as MaterialDoc;
  if (kind.value === "finishes") return { id: "", name: "New finish", type: "opaque", rdiff: 0.8, rspec: 0, sigma: 0 } as FinishDoc;
  return {
    id: "",
    name: "New LED",
    channels: [{ id: "W", name: "White", flux_lm: 10, spectrum: { type: "phosphor_white", cct: 5000 }, pattern: { type: "lambertian", exponent: 1 } }],
    emitter: { shape: "rect", size: [1.6, 1.6] },
  } as LedModel;
}
function newEntry() {
  selectedId.value = null;
  draft.value = template();
  message.value = "";
}
function duplicate() {
  if (!draft.value) return;
  const d = clone(draft.value);
  delete d.builtin;
  d.id = "";
  d.name = `${d.name} (copy)`;
  selectedId.value = null;
  draft.value = d;
}
watch(
  [draft, jsonMode],
  () => {
    if (jsonMode.value && draft.value) jsonText.value = JSON.stringify(draft.value, null, 2);
  },
  { deep: true },
);
function applyJson() {
  try {
    draft.value = JSON.parse(jsonText.value);
    message.value = "";
  } catch (e) {
    message.value = `Not valid JSON: ${(e as Error).message}`;
    messageOk.value = false;
  }
}
async function save() {
  if (!draft.value) return;
  saving.value = true;
  message.value = "";
  try {
    const saved = await api.saveLibraryEntry<AnyDoc>(kind.value, draft.value);
    await ws.loadLibrary();
    selectedId.value = saved.id;
    draft.value = clone(saved);
    messageOk.value = true;
    message.value = "Saved.";
  } catch (e) {
    messageOk.value = false;
    message.value = (e as Error).message;
  } finally {
    saving.value = false;
  }
}
async function remove() {
  if (!selectedId.value || !confirm("Delete this library entry? Setups that use it will need another one.")) return;
  try {
    await api.deleteLibraryEntry(kind.value, selectedId.value);
    await ws.loadLibrary();
    selectedId.value = null;
    draft.value = null;
  } catch (e) {
    messageOk.value = false;
    message.value = (e as Error).message;
  }
}
</script>
