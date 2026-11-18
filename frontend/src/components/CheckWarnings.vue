<template>
  <div v-if="rematched.length || (!rematchOnly && (others.length || missingNotes.length))" class="check-warnings">
    <div v-if="rematched.length" class="notice warn">
      <AlertTriangle :size="15" />
      <div class="rematch">
        <span>
          {{ rematched.length }} face reference{{ rematched.length === 1 ? " was" : "s were" }} re-matched because the design changed. Check them in the 3D view, then store the new faces.
        </span>
        <ul class="small">
          <li v-for="w in rematched" :key="w">{{ short(w) }}</li>
        </ul>
        <button class="btn small" :disabled="busy" @click="accept"><Check :size="14" /> Accept new faces</button>
        <span v-if="error" class="error small">{{ error }}</span>
      </div>
    </div>
    <div v-for="w in rematchOnly ? [] : missingNotes" :key="w" class="notice warn"><AlertTriangle :size="15" /><span>{{ w }}</span></div>
    <div v-for="w in rematchOnly ? [] : others" :key="w" class="notice warn"><AlertTriangle :size="15" /><span>{{ w }}</span></div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";
import { AlertTriangle, Check } from "lucide-vue-next";
import { useWorkspace } from "../composables/useWorkspace";

/** Show only the "re-matched" notice (the tabs that hold face references); the other warnings stay on the Parts and Run tabs. */
defineProps<{ rematchOnly?: boolean }>();

const ws = useWorkspace();
const busy = ref(false);
const error = ref("");

const warnings = computed(() => ws.check.value?.warnings ?? []);
const isRematch = (w: string) => w.includes("was re-matched to face");
const rematched = computed(() => warnings.value.filter(isRematch));
/** The server's one-line summary of the missing group faces; the details come from the structured `groupIssues`. */
const isMissingSummary = (w: string) => w.includes("in analysis groups no longer exist");
const others = computed(() => warnings.value.filter((w) => !isRematch(w) && !isMissingSummary(w)));

/** Up to 3 missing group faces are listed one by one; more than that is one short line that points to the Groups panel. */
const MAX_LISTED = 3;
const missingNotes = computed<string[]>(() => {
  const n = ws.missingCount.value;
  if (!n) return [];
  if (n > MAX_LISTED) return [`${n} face selections in groups are missing. Open the Groups panel to fix them.`];
  const out: string[] = [];
  for (const g of ws.groupsWithIssues.value) {
    for (const m of ws.issuesFor(g.id)) {
      out.push(m.reason === "part" ? `Group ${g.name}: part ${m.part} is missing.` : `Group ${g.name}: face ${m.face} of ${m.part} is missing.`);
    }
  }
  return out;
});
/** "LED 1: face 10 was re-matched to face 8 (the design changed). Please check it." -> "LED 1: face 10 → face 8" */
const short = (w: string) => {
  const m = /^(.*?): face (\d+) was re-matched to face (\d+)/.exec(w);
  return m ? `${m[1]}: face ${m[2]} → face ${m[3]}` : w;
};

async function accept() {
  busy.value = true;
  error.value = "";
  try {
    await ws.acceptFaceMatches();
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    busy.value = false;
  }
}
</script>

<style scoped>
.check-warnings { display: flex; flex-direction: column; gap: 6px; }
.rematch { display: flex; flex-direction: column; gap: 6px; align-items: flex-start; min-width: 0; }
.rematch ul { margin: 0; padding-left: 16px; }
</style>
