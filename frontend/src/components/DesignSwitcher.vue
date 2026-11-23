<template>
  <div class="dsw">
    <button ref="trigger" class="dsw-trigger" :class="{ open }" title="Designs and variants of this project" :aria-expanded="open" @click="open = !open">
      <Layers :size="14" />
      <span class="dsw-trigger-name">{{ currentDesign?.name ?? "Design" }}</span>
      <span class="dsw-sep">›</span>
      <span class="dsw-trigger-name">{{ currentVariant?.name ?? "Variant" }}</span>
      <ChevronDown :size="13" class="dsw-caret" />
    </button>
    <Teleport to="body">
      <div v-if="open" ref="panel" class="dsw-panel" :style="{ left: pos.x + 'px', top: pos.y + 'px' }" role="tree" @mousedown.stop>
        <div class="dsw-head muted small">A design (baseline, dome reflector...) groups its variants (diffuser thicknesses, materials...). Each variant has its own setup.</div>
        <div class="dsw-rows">
          <template v-for="row in rows" :key="row.key">
            <div v-if="row.kind === 'design'" class="dsw-design" role="treeitem">
              <Layers :size="14" />
              <strong>{{ row.design.name }}</strong>
              <span class="muted small dsw-count">{{ row.count }} variant{{ row.count === 1 ? "" : "s" }}</span>
              <button class="dsw-act" title="New variant in this design with another STEP file" aria-label="New variant in this design with another STEP file" @click="act('newVariant', row.design.id)"><Plus :size="14" /></button>
              <button class="dsw-act" title="Rename this design" aria-label="Rename this design" @click="act('renameDesign', row.design.id)"><Pencil :size="13" /></button>
              <button class="dsw-act" :disabled="designs.length < 2" title="Delete this design with all its variants" aria-label="Delete this design" @click="act('deleteDesign', row.design.id)"><Trash2 :size="13" /></button>
            </div>
            <div v-else-if="row.kind === 'variant'" class="dsw-variant" :class="{ current: row.variant.id === currentVariant?.id }" role="treeitem">
              <button class="dsw-open" :disabled="!row.variant.latest" title="Open this variant" @click="pick(row.variant.latest!.id)">
                <Shapes :size="13" />
                <strong class="dsw-vname">{{ row.variant.name }}</strong>
                <span v-if="row.count > 1" class="muted small">{{ row.count }} revs</span>
              </button>
              <button v-if="row.variant.latest" class="dsw-act" title="Duplicate this variant: same geometry, no upload (for other materials or LEDs)" aria-label="Duplicate this variant with the same geometry" @click="act('duplicate', row.variant.latest!.id)"><Copy :size="14" /></button>
              <button v-if="designs.length > 1" class="dsw-act" title="Move this variant to another design" aria-label="Move this variant to another design" @click="act('moveVariant', row.variant.id)"><FolderInput :size="14" /></button>
            </div>
          </template>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { ChevronDown, Copy, FolderInput, Layers, Pencil, Plus, Shapes, Trash2 } from "lucide-vue-next";
import type { Design, Variant } from "../api";

const props = defineProps<{
  designs: Design[];
  variants: Variant[];
  currentRevisionId: string;
}>();
const emit = defineEmits<{
  open: [revisionId: string];
  /** add a variant to this design with another STEP file */
  newVariant: [designId: string];
  /** same geometry, no upload (the id is a revision of the variant to copy) */
  duplicate: [revisionId: string];
  renameDesign: [designId: string];
  deleteDesign: [designId: string];
  moveVariant: [variantId: string];
}>();

type Row =
  | { kind: "design"; key: string; design: Design; count: number }
  | { kind: "variant"; key: string; variant: Variant; count: number };

const currentVariant = computed(() => props.variants.find((v) => v.revisions.includes(props.currentRevisionId)) ?? null);
const currentDesign = computed(() => props.designs.find((d) => d.id === currentVariant.value?.designId) ?? null);

const rows = computed<Row[]>(() => {
  const out: Row[] = [];
  const byCreated = (a: { created: number }, b: { created: number }) => a.created - b.created;
  for (const d of [...props.designs].sort(byCreated)) {
    const vars = props.variants.filter((v) => v.designId === d.id).sort(byCreated);
    out.push({ kind: "design", key: `d:${d.id}`, design: d, count: vars.length });
    for (const v of vars) out.push({ kind: "variant", key: `v:${v.id}`, variant: v, count: v.revisions.length });
  }
  return out;
});

// ── popover ───────────────────────────────────────────────────────────────────────────
const open = ref(false);
const trigger = ref<HTMLElement | null>(null);
const panel = ref<HTMLElement | null>(null);
const pos = ref({ x: 0, y: 0 });

async function place() {
  await nextTick();
  const t = trigger.value?.getBoundingClientRect();
  if (!t) return;
  const w = panel.value?.offsetWidth ?? 460;
  const h = panel.value?.offsetHeight ?? 300;
  pos.value = { x: Math.max(8, Math.min(t.left, window.innerWidth - w - 8)), y: Math.max(8, Math.min(t.bottom + 6, window.innerHeight - h - 8)) };
}
watch(open, (o) => o && place());

function act(kind: "newVariant" | "duplicate" | "renameDesign" | "deleteDesign" | "moveVariant", id: string) {
  open.value = false;
  if (kind === "newVariant") emit("newVariant", id);
  else if (kind === "duplicate") emit("duplicate", id);
  else if (kind === "renameDesign") emit("renameDesign", id);
  else if (kind === "deleteDesign") emit("deleteDesign", id);
  else emit("moveVariant", id);
}
function pick(rid: string) {
  open.value = false;
  if (rid !== props.currentRevisionId) emit("open", rid);
}

function onDown(e: MouseEvent) {
  const t = e.target as Node;
  if (open.value && !panel.value?.contains(t) && !trigger.value?.contains(t)) open.value = false;
}
function onKey(e: KeyboardEvent) {
  if (open.value && e.key === "Escape") {
    e.stopPropagation();
    open.value = false;
  }
}
onMounted(() => {
  document.addEventListener("mousedown", onDown, true);
  document.addEventListener("keydown", onKey, true);
});
onBeforeUnmount(() => {
  document.removeEventListener("mousedown", onDown, true);
  document.removeEventListener("keydown", onKey, true);
});
</script>

<style scoped>
.dsw { display: inline-flex; }
.dsw-trigger {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 28px;
  padding: 0 8px;
  border: 1px solid var(--neutral-4);
  border-radius: var(--radius-md);
  background: var(--neutral-2);
  color: var(--neutral-11);
  font: inherit;
  font-size: 0.78rem;
  cursor: pointer;
  max-width: 420px;
}
.dsw-trigger:hover, .dsw-trigger.open { background: var(--neutral-3); color: var(--neutral-12); }
.dsw-trigger-name { color: var(--neutral-12); font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dsw-sep, .dsw-caret { opacity: 0.6; flex: none; }

.dsw-panel {
  position: fixed;
  z-index: 1000;
  width: 480px;
  max-width: calc(100vw - 16px);
  max-height: min(70vh, 560px);
  display: flex;
  flex-direction: column;
  border: 1px solid var(--neutral-6);
  border-radius: var(--radius-md);
  background: var(--neutral-2);
  color: var(--neutral-12);
  box-shadow: 0 10px 30px rgba(0, 0, 0, 0.35);
}
.dsw-head { padding: 8px 10px; border-bottom: 1px solid var(--neutral-4); }
.dsw-rows { overflow: auto; padding: 4px 0 6px; }

.dsw-design {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 8px 4px 10px;
  margin-top: 6px;
  font-size: 0.84rem;
  border-top: 1px solid var(--neutral-4);
}
.dsw-design:first-child { margin-top: 0; border-top: none; }
.dsw-count { margin-right: auto; margin-left: 4px; }

.dsw-variant { display: flex; align-items: center; gap: 2px; padding: 1px 8px 1px 26px; font-size: 0.82rem; }
.dsw-variant.current { background: var(--neutral-3); }
.dsw-variant.current .dsw-vname { color: var(--accent-11, var(--accent-9)); }
.dsw-open {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 6px;
  border: none;
  background: transparent;
  color: inherit;
  font: inherit;
  font-size: 0.8rem;
  text-align: left;
  cursor: pointer;
  border-radius: var(--radius-sm, 4px);
}
.dsw-open:hover:not(:disabled) { background: var(--neutral-4); }
.dsw-open:disabled { cursor: default; opacity: 0.6; }
.dsw-act {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border: none;
  border-radius: var(--radius-sm, 4px);
  background: transparent;
  color: var(--neutral-11);
  cursor: pointer;
  opacity: 0.35;
}
.dsw-design:hover .dsw-act, .dsw-variant:hover .dsw-act, .dsw-act:focus-visible { opacity: 1; }
.dsw-act:hover:not(:disabled) { background: var(--neutral-4); color: var(--neutral-12); }
.dsw-act:disabled { opacity: 0.15; cursor: default; }
</style>
