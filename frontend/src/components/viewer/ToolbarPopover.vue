<template>
  <div ref="root" class="tpop" @click.stop>
    <slot name="trigger" :open="open" :toggle="toggle" />
    <Transition name="tpop">
      <div v-if="open" class="tpop-panel" :class="{ right: align === 'right' }" role="menu">
        <slot :close="close" />
      </div>
    </Transition>
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from "vue";

withDefaults(defineProps<{ align?: "left" | "right" }>(), { align: "left" });

/** A small temporary overlay under a toolbar button: closes on a click outside it and on Esc. */
const open = ref(false);
const root = ref<HTMLElement | null>(null);

const toggle = () => (open.value = !open.value);
const close = () => (open.value = false);

function onPointerDown(e: PointerEvent) {
  if (root.value && !root.value.contains(e.target as Node)) close();
}
function onKey(e: KeyboardEvent) {
  if (e.key === "Escape") close();
}
watch(open, (on) => {
  if (on) {
    document.addEventListener("pointerdown", onPointerDown, true);
    document.addEventListener("keydown", onKey);
  } else {
    document.removeEventListener("pointerdown", onPointerDown, true);
    document.removeEventListener("keydown", onKey);
  }
});
onBeforeUnmount(() => {
  document.removeEventListener("pointerdown", onPointerDown, true);
  document.removeEventListener("keydown", onKey);
});

defineExpose({ close });
</script>

<style>
.tpop { position: relative; display: inline-flex; }
.tpop-panel {
  position: absolute; top: calc(100% + 8px); left: 0; z-index: 95; min-width: 260px; padding: 4px;
  display: flex; flex-direction: column; gap: 2px;
  background: var(--neutral-1); border: 1px solid var(--neutral-4); border-radius: var(--radius-lg);
  box-shadow: var(--shadow-md);
}
.tpop-panel.right { left: auto; right: 0; }
.tpop-enter-active, .tpop-leave-active { transition: opacity 0.12s ease, transform 0.12s ease; }
.tpop-enter-from, .tpop-leave-to { opacity: 0; transform: translateY(-4px); }
.tpop-head { padding: 4px 8px 2px; font-size: 0.68rem; font-weight: 700; color: var(--neutral-10); text-transform: uppercase; letter-spacing: 0.04em; }
.tpop-row {
  display: flex; align-items: center; gap: 9px; width: 100%; padding: 6px 8px;
  border: none; border-radius: var(--radius-md); background: transparent; color: var(--neutral-12);
  font: inherit; font-size: 0.82rem; text-align: left; cursor: pointer;
}
.tpop-row:hover:not(:disabled) { background: var(--neutral-3); }
.tpop-row:disabled { opacity: 0.45; cursor: default; }
.tpop-row > svg { flex: none; color: var(--neutral-10); }
.tpop-row.on > svg { color: var(--accent-9); }
.tpop-text { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 1px; }
.tpop-text strong { font-weight: 600; }
.tpop-text small { font-size: 0.72rem; color: var(--neutral-11); }
.tpop-check { flex: none; width: 14px; display: inline-flex; color: var(--accent-9); }
.tpop-sep { height: 1px; margin: 2px 4px; background: var(--neutral-4); }
</style>
