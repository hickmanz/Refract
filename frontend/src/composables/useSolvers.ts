// Which solver traces the rays: "cpu" (Embree + Numba, always there) or "gpu" (NVIDIA Warp on CUDA, when this machine has it).
// One choice for the whole app (the Run tab and the live preview), remembered between sessions.
import { computed, ref, watch } from "vue";
import { api, type SolverInfo } from "../api";

const KEY = "refract.solver";
const solvers = ref<SolverInfo[]>([{ id: "cpu", available: true, name: "CPU (Embree + Numba)", reason: null }]);
const chosen = ref<string>(localStorage.getItem(KEY) === "gpu" ? "gpu" : "cpu");
let loaded = false;

watch(chosen, (v) => localStorage.setItem(KEY, v));

export function useSolvers() {
  if (!loaded) {
    loaded = true;
    api.solvers().then((d) => (solvers.value = d.solvers)).catch(() => undefined);
  }
  const gpu = computed(() => solvers.value.find((s) => s.id === "gpu") ?? null);
  /** What is actually used: the GPU only when it was chosen and exists. */
  const solver = computed(() => (chosen.value === "gpu" && gpu.value?.available ? "gpu" : "cpu"));
  return { solvers, gpu, chosen, solver };
}
