<template>
  <div class="form-grid">
    <label class="field"><span>Name</span><input v-model="doc.name" :disabled="readonly" /></label>
    <label class="field">
      <span>Surface type</span>
      <select v-model="doc.type" :disabled="readonly">
        <option value="texture">Texture (roughness only: VDI, MT, polished; works on any material)</option>
        <option value="dielectric">Transparent surface (roughness for a volume material; older entries)</option>
        <option value="opaque">Surface preset (colour, reflectances and roughness in one: paint, plastic, coating)</option>
      </select>
    </label>
    <label class="field">
      <span>Roughness (RMS slope σ)</span>
      <input v-model.number="doc.sigma" type="number" step="0.01" min="0" max="1.5" :disabled="readonly" />
      <small class="muted">0 is a perfect mirror/polish. About 0.05 is a light frosting, 0.3 or more is a heavy texture (e.g. VDI 30). A rule of thumb from the roughness Ra: σ ≈ 0.12 × √(Ra in µm).</small>
    </label>
    <label v-if="doc.type === 'texture'" class="check small">
      <input v-model="doc.inherit" type="checkbox" :disabled="readonly" /> keep the roughness defined in the material (ignore the value above)
    </label>
    <template v-if="doc.type === 'opaque'">
      <div class="field">
        <span>Colour</span>
        <div class="color-row">
          <input type="color" :value="doc.color || '#ffffff'" :disabled="readonly" @input="pickColor($event)" />
          <code class="muted">{{ doc.color || "neutral (grey)" }}</code>
          <button v-if="doc.color && !readonly" class="btn small" type="button" @click="doc.color = undefined">No colour</button>
        </div>
        <small class="muted">The colour of the surface in white light, for example a yellow housing. Picking one also sets the diffuse reflectance to that colour's brightness, which you can still change below. It changes the colour of light that bounces off this surface.</small>
      </div>
      <label v-if="doc.color && (doc.rspec ?? 0) > 0" class="check small"><input v-model="doc.tint_specular" type="checkbox" :disabled="readonly" /> also colour the mirror part (coloured metal)</label>
      <label class="field">
        <span>Diffuse reflectance</span>
        <input v-model.number="doc.rdiff" type="number" step="0.01" min="0" max="1" :disabled="readonly" />
        <small class="muted">White paint about 0.85 to 0.95. Black about 0.04.</small>
      </label>
      <label class="field">
        <span>Specular reflectance</span>
        <input v-model.number="doc.rspec" type="number" step="0.01" min="0" max="1" :disabled="readonly" />
        <small class="muted">Mirror-like part (polished metal or a mirror coating). Diffuse + specular must not exceed 1.</small>
      </label>
    </template>
  </div>
</template>

<script setup lang="ts">
import type { FinishDoc } from "../../api";
const props = defineProps<{ doc: FinishDoc; readonly?: boolean }>();

function pickColor(e: Event) {
  const hex = (e.target as HTMLInputElement).value;
  props.doc.color = hex;
  // the reflectance level follows the colour's luminance (linear light), so a dark colour is also a dark surface
  const lin = [1, 3, 5].map((i) => {
    const v = parseInt(hex.slice(i, i + 2), 16) / 255;
    return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
  });
  const y = 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2];
  const total = (props.doc.rspec ?? 0) > 0 ? Math.max(0, 1 - (props.doc.rspec ?? 0)) : 1;
  props.doc.rdiff = Math.round(Math.min(y, total) * 100) / 100;
}
</script>
