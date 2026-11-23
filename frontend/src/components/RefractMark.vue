<template>
  <svg class="refract-mark" :width="size" :height="size" viewBox="0 0 64 64" fill="none" aria-hidden="true">
    <!-- the surface -->
    <path d="M8 34h48" stroke="currentColor" stroke-opacity="0.6" stroke-width="3.2" stroke-linecap="round" />
    <!-- one ray, bent toward the normal where it enters the surface -->
    <path d="M16.7 11 36 34l11.1 23.5" stroke="var(--accent-9)" stroke-width="5.2" stroke-linecap="round" stroke-linejoin="round" />
  </svg>
</template>

<script setup lang="ts">
withDefaults(defineProps<{ size?: number }>(), { size: 24 });
</script>

<style scoped>
.refract-mark {
  flex: none;
  color: var(--neutral-12);
}
</style>
