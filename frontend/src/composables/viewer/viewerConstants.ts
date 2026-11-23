import type { Component } from "vue";
import {
  ExplodeIcon,
  WireframeIcon,
  ShadedIcon,
  WireframeOnShadedIcon,
  HiddenLineIcon,
  XrayIcon,
} from "../../components/icons/customIcons";

export { ExplodeIcon };

export const drawModes: {
  id: string;
  label: string;
  icon: Component;
  mode: number;
}[] = [
  {
    id: "wireframeOnShaded",
    label: "Wireframe On Shaded",
    icon: WireframeOnShadedIcon,
    mode: 2,
  },
  { id: "shaded", label: "Shaded", icon: ShadedIcon, mode: 1 },
  { id: "wireframe", label: "Wireframe (display mesh, not the ray-traced one)", icon: WireframeIcon, mode: 0 },
  { id: "hiddenLine", label: "Hidden Line", icon: HiddenLineIcon, mode: 3 },
  { id: "xray", label: "XRay", icon: XrayIcon, mode: 4 },
];
