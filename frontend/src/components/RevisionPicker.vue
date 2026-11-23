<template>
  <label v-if="revisions.length > 1" class="rvp" title="Revisions of this variant (a re-mesh is listed under the revision it was made from)">
    <select :value="currentRevisionId" aria-label="Revision of this variant" @change="onChange">
      <option v-for="r in ordered" :key="r.id" :value="r.id">{{ r.mesh ? "\u00a0\u00a0\u21b3 " : "" }}{{ revisionName(r.rev, revisions) }}</option>
    </select>
  </label>
</template>

<script setup lang="ts">
import { computed } from "vue";
import type { RevisionMeta } from "../api";
import { revisionName } from "../composables/useWorkspace";

const props = defineProps<{
  /** the revisions of the variant being edited */
  revisions: RevisionMeta[];
  currentRevisionId: string;
}>();
const emit = defineEmits<{ open: [revisionId: string] }>();

/** Newest first; a re-mesh sits right under the revision it was made from. */
const ordered = computed(() => {
  const ids = new Set(props.revisions.map((r) => r.id));
  const newest = (a: RevisionMeta, b: RevisionMeta) => b.created - a.created;
  const out: { rev: RevisionMeta; mesh: boolean }[] = [];
  for (const r of props.revisions.filter((x) => !x.remeshedFrom || !ids.has(x.remeshedFrom)).sort(newest)) {
    out.push({ rev: r, mesh: false });
    for (const m of props.revisions.filter((x) => x.remeshedFrom === r.id).sort(newest)) out.push({ rev: m, mesh: true });
  }
  return out.map((o) => ({ ...o, id: o.rev.id }));
});

function onChange(e: Event) {
  const id = (e.target as HTMLSelectElement).value;
  if (id && id !== props.currentRevisionId) emit("open", id);
}
</script>

<style scoped>
.rvp { display: inline-flex; }
.rvp select {
  height: 28px;
  padding: 0 6px;
  border: 1px solid var(--neutral-4);
  border-radius: var(--radius-md);
  background: var(--neutral-2);
  color: var(--neutral-12);
  font: inherit;
  font-size: 0.78rem;
  max-width: 220px;
  cursor: pointer;
}
.rvp select:hover { background: var(--neutral-3); }
</style>
