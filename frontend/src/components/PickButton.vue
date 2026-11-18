<template>
  <button type="button" class="btn small pick-btn" :class="{ picking: active }" :aria-pressed="active" :title="active ? 'Stop picking (Esc)' : idleTitle" @click="$emit('click')">
    <template v-if="active">
      <span class="pick-dot" aria-hidden="true" />
      <Check :size="14" />
      {{ activeLabel }}
    </template>
    <template v-else>
      <MousePointerClick :size="14" />
      {{ label }}
    </template>
  </button>
</template>

<script setup lang="ts">
import { Check, MousePointerClick } from "lucide-vue-next";

withDefaults(
  defineProps<{
    active: boolean;
    /** Text while idle. */
    label?: string;
    /** Text while picking: says what a click does now, and how to stop. */
    activeLabel?: string;
    idleTitle?: string;
  }>(),
  { label: "Pick faces in 3D", activeLabel: "Picking: click to finish", idleTitle: "Then click faces in the 3D view" },
);
defineEmits<{ click: [] }>();
</script>
