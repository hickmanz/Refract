<template>
  <div class="modal-backdrop" @click.self="$emit('close')">
    <div class="modal wide">
      <h3>Settings</h3>

      <section class="settings-section">
        <h4>Appearance</h4>
        <div class="appearance-row">
          <span class="muted small">Theme</span>
          <div class="seg-pick" role="radiogroup" aria-label="Theme">
            <button
              v-for="m in THEME_CHOICES"
              :key="m.id"
              role="radio"
              :aria-checked="themeMode === m.id"
              :class="{ on: themeMode === m.id }"
              @click="setThemeMode(m.id)"
            >
              {{ m.label }}
            </button>
          </div>
        </div>
        <div class="appearance-row">
          <span class="muted small">Accent</span>
          <div class="accent-dots" role="radiogroup" aria-label="Accent colour">
            <button
              v-for="a in ACCENTS"
              :key="a.id"
              role="radio"
              class="accent-dot"
              :class="{ on: accentId === a.id }"
              :style="{ '--dot': a.swatch }"
              :title="a.label"
              :aria-label="a.label"
              :aria-checked="accentId === a.id"
              @click="setAccent(a.id)"
            />
          </div>
        </div>
      </section>

      <hr class="modal-sep" />

      <section class="settings-section">
        <h4>Data folder</h4>
        <p class="muted small">Projects, revisions, setups and simulation results are stored here.</p>
        <p v-if="cfg?.fromEnvironment" class="notice warn" style="margin: 6px 0">
          <AlertTriangle :size="15" />
          <span>The folder is fixed by the <code>REFRACT_DATA</code> environment variable and cannot be changed here.</span>
        </p>
        <div class="folder-row">
          <input v-model="folder" type="text" placeholder="D:\RefractData" :disabled="cfg?.fromEnvironment || busy" @keydown.enter="apply" />
          <button class="btn" :disabled="cfg?.fromEnvironment || busy" @click="browse"><FolderOpen :size="15" /> Browse…</button>
        </div>
        <label class="field row">
          <input v-model="copyExisting" type="checkbox" :disabled="cfg?.fromEnvironment || busy" />
          <span>Copy my current projects into the new folder</span>
        </label>
        <div class="modal-actions" style="justify-content: flex-start">
          <button class="btn primary" :disabled="!changed || busy || cfg?.fromEnvironment" @click="apply">Use this folder</button>
          <button v-if="cfg?.configuredDir" class="btn" :disabled="busy" @click="resetFolder">Back to default</button>
        </div>
        <p v-if="folderMsg" class="small" :class="folderOk ? 'ok-text' : 'error'">{{ folderMsg }}</p>
        <p class="muted small">Now using: <code>{{ cfg?.dataDir }}</code></p>
      </section>

      <hr class="modal-sep" />

      <section class="settings-section">
        <h4>Updates</h4>
        <p class="muted small">Refract {{ cfg?.version }}. Where the app looks for a newer installer:</p>
        <label class="field">
          <span>Update source</span>
          <select v-model="upd.source" class="text-input" :disabled="updateInstalling" @change="saveUpdate">
            <option value="github">GitHub releases (hickmanz/Refract)</option>
            <option value="custom">A folder or web address of my own</option>
          </select>
        </label>
        <template v-if="upd.source === 'custom'">
          <div class="folder-row">
            <input
              v-model="upd.location"
              type="text"
              placeholder="D:\Releases  or  \\server\share\Refract  or  https://example.com/refract"
              :disabled="updateInstalling"
              @change="saveUpdate"
              @keydown.enter="saveUpdate"
            />
            <button class="btn" :disabled="updateInstalling" @click="browseUpdate"><FolderOpen :size="15" /> Browse…</button>
          </div>
          <p class="muted small">
            It holds <code>Refract-Setup-&lt;version&gt;.exe</code>; the highest version is offered. A <code>latest.json</code> next to it
            (<code>{"version", "file", "sha256", "notes"}</code>, written by <code>packaging\build.ps1</code>) is used when present and is required for a web address.
          </p>
        </template>
        <label v-else class="field row">
          <input v-model="upd.prerelease" type="checkbox" :disabled="updateInstalling" @change="saveUpdate" />
          <span>Include pre-releases (betas)</span>
        </label>
        <label class="field row">
          <input v-model="upd.auto" type="checkbox" :disabled="updateInstalling" @change="saveUpdate" />
          <span>Check for updates when the app starts</span>
        </label>

        <div class="modal-actions" style="justify-content: flex-start">
          <button class="btn" :disabled="updateChecking || updateInstalling" @click="check">
            <RefreshCw :size="15" /> {{ updateChecking ? "Checking…" : "Check now" }}
          </button>
          <button v-if="updateCheck?.available && cfg?.canInstallUpdates" class="btn primary" :disabled="updateInstalling" @click="installUpdate">
            <Download :size="15" /> {{ updateInstalling ? "Updating…" : `Install ${updateCheck.latest}` }}
          </button>
        </div>
        <div v-if="updateInstalling && updateProgress" class="progress thin" style="margin-top: 6px">
          <div class="progress-bar" :style="{ width: updateProgress.total ? Math.round((updateProgress.done / updateProgress.total) * 100) + '%' : '30%' }" />
        </div>
        <p v-if="updateInstalling" class="muted small">
          {{ updateProgress?.state === "launching" ? "Installing the update. Refract restarts by itself." : `Downloading… ${updateProgress?.total ? fmtMB(updateProgress.done) + " of " + fmtMB(updateProgress.total) : ""}` }}
        </p>
        <p v-if="updateError" class="small error">{{ updateError }}</p>
        <template v-else-if="updateCheck && !updateInstalling">
          <p v-if="updateCheck.available" class="small ok-text">
            Version {{ updateCheck.latest }} is available.
            <a v-if="updateCheck.offer?.page" :href="updateCheck.offer.page" target="_blank" rel="noopener">Release notes</a>
          </p>
          <p v-else-if="updateCheck.latest" class="small ok-text">You have the newest version ({{ updateCheck.current }}).</p>
          <p v-else class="muted small">No installer was found at this source.</p>
          <p v-if="updateCheck.available && !cfg?.canInstallUpdates" class="muted small">
            Updates install from the installed app only (this is a development copy).
          </p>
          <pre v-if="updateCheck.available && updateCheck.offer?.notes" class="update-notes">{{ updateCheck.offer.notes }}</pre>
        </template>
      </section>

      <hr class="modal-sep" />

      <section class="settings-section">
        <h4>This app</h4>
        <p class="muted small">Refract version {{ cfg?.version }}.</p>
        <p v-if="cfg?.mode === 'window'" class="muted small">Close the window to quit the app.</p>
        <template v-else>
          <div class="modal-actions" style="justify-content: flex-start">
            <button class="btn" @click="quit"><Power :size="15" /> Quit the app</button>
          </div>
          <p v-if="quitMsg" class="muted small">{{ quitMsg }}</p>
        </template>
      </section>

      <div class="modal-actions">
        <button class="btn" @click="$emit('close')">Close</button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { AlertTriangle, Download, FolderOpen, Power, RefreshCw } from "lucide-vue-next";
import { api, type AppConfig, type UpdateSettings } from "../api";
import { ACCENTS, accentId, setAccent, setThemeMode, themeMode, type ThemeMode } from "../composables/useTheme";
import {
  checkForUpdate,
  fmtMB,
  installUpdate,
  updateCheck,
  updateChecking,
  updateError,
  updateInstalling,
  updateProgress,
} from "../composables/useUpdater";

const emit = defineEmits<{ close: []; changed: [] }>();

const THEME_CHOICES: { id: ThemeMode; label: string }[] = [
  { id: "dark", label: "Dark" },
  { id: "light", label: "Light" },
  { id: "system", label: "System" },
];

const cfg = ref<AppConfig | null>(null);
const folder = ref("");
const copyExisting = ref(true);
const busy = ref(false);
const folderMsg = ref("");
const folderOk = ref(false);
const quitMsg = ref("");

const changed = computed(() => !!cfg.value && folder.value.trim() !== "" && folder.value.trim() !== cfg.value.dataDir);

const upd = ref<UpdateSettings>({ source: "github", location: "", auto: true, prerelease: false });

async function loadConfig() {
  cfg.value = await api.config();
  folder.value = cfg.value.dataDir;
  upd.value = { ...cfg.value.update };
}

async function saveUpdate() {
  updateError.value = "";
  try {
    upd.value = await api.setUpdateSettings({ ...upd.value, location: upd.value.location.trim() });
    updateCheck.value = null; // the old answer belongs to the old source
  } catch (e) {
    updateError.value = (e as Error).message;
  }
}

async function browseUpdate() {
  try {
    const res = await api.pickFolder(upd.value.location || undefined);
    if (res.path) {
      upd.value.location = res.path;
      await saveUpdate();
    } else if (res.error) updateError.value = `The folder dialog could not be opened (${res.error}). Type or paste the path instead.`;
  } catch (e) {
    updateError.value = (e as Error).message;
  }
}

async function check() {
  await saveUpdate(); // use what is typed, even if the field was not left yet
  if (updateError.value) return;
  await checkForUpdate();
}

async function browse() {
  folderMsg.value = "";
  busy.value = true;
  try {
    const res = await api.pickFolder(folder.value || undefined);
    if (res.path) folder.value = res.path;
    else if (res.error) {
      folderOk.value = false;
      folderMsg.value = `The folder dialog could not be opened (${res.error}). Type or paste the path instead.`;
    }
  } catch (e) {
    folderOk.value = false;
    folderMsg.value = (e as Error).message;
  } finally {
    busy.value = false;
  }
}

async function change(target: string, copy: boolean, okMessage: string) {
  busy.value = true;
  folderMsg.value = "";
  try {
    cfg.value = await api.setDataDir(target, copy);
    folder.value = cfg.value.dataDir;
    folderOk.value = true;
    folderMsg.value = cfg.value.copied ? `${okMessage} Copied ${cfg.value.copied} item(s).` : okMessage;
    emit("changed");
  } catch (e) {
    folderOk.value = false;
    folderMsg.value = (e as Error).message;
  } finally {
    busy.value = false;
  }
}
const apply = () => (changed.value && !busy.value ? change(folder.value.trim(), copyExisting.value, "Now using this folder.") : undefined);
const resetFolder = () => change("", false, "Back to the default folder.");

async function quit() {
  if (!confirm("Quit Refract? Running simulations will be cancelled.")) return;
  try {
    await api.quit();
  } catch {
    /* it is going away */
  }
  quitMsg.value = "The app has closed. You can close this tab.";
}

onMounted(loadConfig);
</script>

<style scoped>
.appearance-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  min-height: 34px;
}
.seg-pick {
  display: inline-flex;
  padding: 3px;
  gap: 2px;
  border-radius: var(--radius-lg);
  background: var(--neutral-1);
  border: 1px solid var(--hairline);
}
.seg-pick button {
  padding: 4px 14px;
  border: none;
  border-radius: var(--radius-md);
  background: transparent;
  color: var(--neutral-10);
  font: inherit;
  font-size: 0.8rem;
  font-weight: 500;
  cursor: pointer;
  transition: background var(--dur) var(--ease), color var(--dur) var(--ease);
}
.seg-pick button:hover {
  color: var(--neutral-12);
}
.seg-pick button.on {
  background: var(--neutral-4);
  color: var(--neutral-12);
  box-shadow: var(--shadow-sm);
}
.accent-dots {
  display: inline-flex;
  gap: 10px;
}
.accent-dot {
  width: 22px;
  height: 22px;
  padding: 0;
  border: none;
  border-radius: 50%;
  background: var(--dot);
  cursor: pointer;
  box-shadow: inset 0 0 0 1px rgb(255 255 255 / 0.18);
  transition: transform var(--dur) var(--ease), box-shadow var(--dur) var(--ease);
}
.accent-dot:hover {
  transform: scale(1.12);
}
.accent-dot.on {
  box-shadow: 0 0 0 2px var(--neutral-2), 0 0 0 4px var(--dot);
}
.ok-text {
  color: var(--radix-green-9);
}
code {
  font-size: 0.78rem;
  word-break: break-all;
}
.update-notes {
  max-height: 140px;
  overflow: auto;
  margin: 4px 0 0;
  padding: 6px 8px;
  font-size: 0.75rem;
  white-space: pre-wrap;
  border: 1px solid var(--hairline-strong);
  border-radius: var(--radius-md);
  background: var(--neutral-1);
}
</style>
