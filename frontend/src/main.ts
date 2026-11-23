import "./migrateStorage"; // first: moves the old lightsim.* settings to refract.* before anything reads them
import { createApp } from "vue";
import { createRouter, createWebHistory } from "vue-router";
import "@fontsource-variable/inter"; // bundled: the app is offline
import App from "./App.vue";
import "./styles/index.css";
import "./styles/app.css";
import "./styles/refract.css";
import "./styles/refract-ui.css"; // the Refract look; keep last so it refines the rules above

// Radix colour tokens (light + dark, switched by the .dark class on <html>)
import "@radix-ui/colors/sky.css";
import "@radix-ui/colors/sky-dark.css";
import "@radix-ui/colors/slate.css";
import "@radix-ui/colors/slate-dark.css";
import "@radix-ui/colors/red.css";
import "@radix-ui/colors/red-dark.css";
import "@radix-ui/colors/amber.css";
import "@radix-ui/colors/amber-dark.css";
import "@radix-ui/colors/green.css";
import "@radix-ui/colors/green-dark.css";
import "@radix-ui/colors/blue.css";
import "@radix-ui/colors/blue-dark.css";

import { initTheme } from "./composables/useTheme";
import { api } from "./api";
import HomeView from "./views/HomeView.vue";
import RevisionView from "./views/RevisionView.vue";

initTheme();

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", name: "home", component: HomeView },
    { path: "/r/:id", name: "revision", component: RevisionView, props: true },
    {
      // open a project at its newest revision
      path: "/p/:pid",
      component: HomeView,
      beforeEnter: async (to) => {
        try {
          const project = await api.getProject(String(to.params.pid));
          const latest = project.revisionList?.[0];
          return latest ? `/r/${latest.id}` : "/";
        } catch {
          return "/";
        }
      },
    },
    { path: "/:pathMatch(.*)*", redirect: "/" },
  ],
});

createApp(App).use(router).mount("#app");
