import { defineComponent, h } from "vue";

export const ExplodeIcon = defineComponent({
  name: "ExplodeIcon",
  render() {
    return h(
      "svg",
      { xmlns: "http://www.w3.org/2000/svg", viewBox: "0 0 100 100" },
      [
        h("path", {
          d: "M59.1,58.4c-0.6,0.4-1,1-1,1.7v26.8L50,91.6l-8.1-4.7V60.1 c0-0.7-0.4-1.3-1-1.7L18,44.5V30.2L49.1,46c0.3,0.1,0.6,0.2,0.9,0.2c0.3,0,0.6-0.1,0.9-0.2L82,30.3v14.3L59.1,58.4z",
          opacity: "var(--icon-face-front, 0.2)",
        }),
        h("path", {
          d: "M92.8,39.2c-0.6-0.4-1.4-0.3-2,0L84,43.3V29.2l8.7-4.4c0.7-0.3,1.1-1,1.1-1.8s-0.4-1.4-1.1-1.8 l-41.8-21c-0.6-0.3-1.2-0.3-1.8,0L7.2,21.3c-0.7,0.3-1.1,1-1.1,1.8s0.4,1.4,1.1,1.8l8.7,4.4v14.1l-6.8-4.1c-0.6-0.4-1.4-0.4-2,0 s-1,1-1,1.7l0.1,37.9c0,0.7,0.4,1.3,1,1.7l31.6,19.1c0.3,0.2,0.7,0.3,1,0.3s0.7-0.1,1-0.3c0.6-0.4,1-0.9,1-1.6v-8.8l7.6,4.4 c0.2,0.1,0.3,0.1,0.5,0.1s0.3,0,0.5-0.1l7.6-4.4V98c0,0.7,0.4,1.4,1,1.7c0.3,0.2,0.6,0.3,1,0.3s0.7-0.1,1-0.3l31.6-19.2 c0.6-0.4,1-1,1-1.7l0.1-37.9C93.8,40.2,93.4,39.6,92.8,39.2z M50,4.2L87.4,23L50,42L12.6,23L50,4.2z M37.9,94.5L10.3,77.7l-0.1-33.2 l27.7,16.7V94.5z M59.1,58.4c-0.6,0.4-1,1-1,1.7v26.8L50,91.6l-8.1-4.7V60.1c0-0.7-0.4-1.3-1-1.7L18,44.5V30.2L49.1,46 c0.3,0.1,0.6,0.2,0.9,0.2c0.3,0,0.6-0.1,0.9-0.2L82,30.3v14.3L59.1,58.4z M89.7,77.7L62.1,94.5V61.2l27.7-16.7L89.7,77.7z",
        }),
      ],
    );
  },
});

export const WireframeIcon = defineComponent({
  name: "WireframeIcon",
  render() {
    const inner = { fill: "var(--neutral-9)" };
    return h(
      "svg",
      { xmlns: "http://www.w3.org/2000/svg", viewBox: "0 0 19 21" },
      [
        // Inner wireframe edges (lighter grey)
        h("path", {
          ...inner,
          d: "M9.5,10.7c-.2,0-.4-.2-.4-.4V.5c0-.2.2-.4.4-.4s.4.2.4.4v9.8c0,.2-.2.4-.4.4Z",
        }),
        h("path", {
          ...inner,
          d: "M.5,15.5c-.1,0-.3,0-.3-.2-.1-.2,0-.4.1-.5l9-5c.1,0,.3,0,.4,0l8.9,5c.2.1.2.3.1.5-.1.2-.3.2-.5.1l-8.7-4.9L.7,15.5c0,0-.1,0-.2,0Z",
        }),
        // Solid structure (inherits currentColor = --neutral-12)
        h("path", {
          d: "M9.5,21c-.4,0-.9-.1-1.2-.3l-7-4c-.8-.4-1.3-1.3-1.3-2.2V6.5c0-.9.5-1.7,1.2-2.2L8.3.3c.8-.4,1.7-.4,2.5,0l7,4c.8.4,1.3,1.3,1.3,2.2h0v8c0,.9-.5,1.7-1.2,2.2l-7,4c-.4.2-.8.3-1.2.3ZM9.5,1c-.3,0-.5,0-.7.2L1.7,5.2c-.5.3-.7.8-.7,1.3v8c0,.5.3,1,.8,1.3l7,4c.5.3,1,.3,1.5,0l7-4c.5-.3.7-.8.7-1.3v-8c0-.5-.3-1-.8-1.3l-7-4c-.2-.1-.5-.2-.8-.2Z",
        }),
        h("path", {
          d: "M9.5,11c0,0-.2,0-.2,0L.6,5.9c-.2-.1-.3-.4-.2-.7.1-.2.4-.3.7-.2l8.5,4.9,8.5-4.9c.2-.1.5,0,.7.2.1.2,0,.5-.2.7l-8.7,5c0,0-.2,0-.2,0Z",
        }),
        h("path", {
          d: "M9.5,21c-.3,0-.5-.2-.5-.5v-10c0-.3.2-.5.5-.5s.5.2.5.5v10c0,.3-.2.5-.5.5Z",
        }),
      ],
    );
  },
});

export const ShadedIcon = defineComponent({
  name: "ShadedIcon",
  render() {
    return h(
      "svg",
      { xmlns: "http://www.w3.org/2000/svg", viewBox: "0 0 19 21" },
      [
        // Outline (inherits currentColor = --neutral-12)
        h("path", {
          d: "M9.5,21c-.4,0-.9-.1-1.3-.3l-7-4C.5,16.2,0,15.4,0,14.5V6.5c0-.9.5-1.7,1.2-2.2L8.3.3c.8-.4,1.7-.4,2.5,0l7,4c.8.4,1.3,1.3,1.3,2.2h0v8c0,.9-.5,1.7-1.2,2.2l-7,4c-.4.2-.8.3-1.2.3ZM0,14.5V6.5M9.5,1c-.3,0-.5,0-.7.2L1.7,5.2c-.5.3-.7.8-.7,1.3v8c0,.5.3,1,.8,1.3l7,4c.5.3,1,.3,1.5,0l7-4c.5-.3.7-.8.7-1.3v-8c0-.5-.3-1-.8-1.3l-7-4c-.2-.1-.5-.2-.8-.2Z",
        }),
        // Left face
        h("path", {
          fill: "var(--neutral-9)",
          d: "M1,14.5c0,.5.3,1,.8,1.3l7,4c.3.1.5.2.8.2v-9.6L1.3,5.7c-.2.2-.3.5-.3.8v8Z",
        }),
        // Top face
        h("path", {
          fill: "var(--neutral-10)",
          d: "M10.3,1.2c-.2-.1-.5-.2-.8-.2s-.5,0-.7.2L1.7,5.2c-.2.1-.3.3-.5.5l8.3,4.8,8.2-4.7c-.1-.2-.3-.4-.5-.5l-7-4Z",
        }),
        // Right face
        h("path", {
          fill: "var(--neutral-6)",
          d: "M17.3,15.8c.5-.3.7-.8.7-1.3v-8c0-.3,0-.6-.2-.8l-8.2,4.7v9.6c.2,0,.5,0,.7-.2l7-4Z",
        }),
      ],
    );
  },
});

export const WireframeOnShadedIcon = defineComponent({
  name: "WireframeOnShadedIcon",
  render() {
    const edge = { fill: "var(--neutral-12)" };
    return h(
      "svg",
      { xmlns: "http://www.w3.org/2000/svg", viewBox: "0 0 19 21" },
      [
        h("polygon", {
          points: ".4 15.5 9.5 20.6 9.5 10.5 .8 5.5 .4 15.5",
          fill: "var(--neutral-9)",
        }),
        h("polygon", {
          points: "9.5 20.3 9.5 10.5 18.1 5.6 18.2 15.5 9.5 20.3",
          fill: "var(--neutral-6)",
        }),
        h("polygon", {
          points: ".8 5.5 9.5 10.5 18.1 5.6 9.7 .5 .8 5.5",
          fill: "var(--neutral-10)",
        }),
        // Inner edge lines
        h("path", {
          ...edge,
          d: "M9.5,10.9c0,0-.1,0-.2,0L.6,5.8c-.2-.1-.2-.3-.1-.5.1-.2.3-.2.5-.1l8.5,4.9,8.5-4.9c.2-.1.4,0,.5.1s0,.4-.1.5l-8.7,5c0,0-.1,0-.2,0Z",
        }),
        h("path", {
          ...edge,
          d: "M9.5,20.9c-.2,0-.4-.2-.4-.4v-10c0-.2.2-.4.4-.4s.4.2.4.4v10c0,.2-.2.4-.4.4Z",
        }),
        // Solid outline (inherits currentColor = --neutral-12)
        h("path", {
          d: "M9.5,21c-.4,0-.9-.1-1.2-.3l-7-4C.5,16.2,0,15.4,0,14.5V6.5c0-.9.5-1.7,1.2-2.2L8.3.3c.8-.4,1.7-.4,2.5,0l7,4c.8.4,1.3,1.3,1.3,2.2h0v8c0,.9-.5,1.7-1.2,2.2l-7,4c-.4.2-.8.3-1.2.3ZM9.5,1c-.3,0-.5,0-.7.2L1.7,5.2c-.5.3-.7.8-.7,1.3v8c0,.5.3,1,.8,1.3l7,4c.5.3,1,.3,1.5,0l7-4c.5-.3.7-.8.7-1.3v-8c0-.5-.3-1-.8-1.3l-7-4c-.2-.1-.5-.2-.8-.2Z",
        }),
      ],
    );
  },
});

export const HiddenLineIcon = defineComponent({
  name: "HiddenLineIcon",
  render() {
    const dashed = { fill: "var(--neutral-9)" };
    return h(
      "svg",
      { xmlns: "http://www.w3.org/2000/svg", viewBox: "0 0 19 21" },
      [
        // Dashed lines (lighter grey)
        h("path", {
          ...dashed,
          d: "M9.5,2.4c-.2,0-.4-.2-.4-.4V.5c0-.2.2-.4.4-.4s.4.2.4.4v1.5c0,.2-.2.4-.4.4Z",
        }),
        h("path", {
          ...dashed,
          d: "M9.5,6.9c-.2,0-.4-.2-.4-.4v-2.3c0-.2.2-.4.4-.4s.4.2.4.4v2.3c0,.2-.2.4-.4.4Z",
        }),
        h("path", {
          ...dashed,
          d: "M9.5,10.7c-.2,0-.4-.2-.4-.4v-1.5c0-.2.2-.4.4-.4s.4.2.4.4v1.5c0,.2-.2.4-.4.4Z",
        }),
        h("path", {
          ...dashed,
          d: "M18.4,15.5c0,0-.1,0-.2,0l-1.3-.7c-.2-.1-.2-.3-.1-.5.1-.2.3-.2.5-.1l1.3.7c.2.1.2.3.1.5,0,.1-.2.2-.3.2Z",
        }),
        h("path", {
          ...dashed,
          d: "M15,13.6c0,0-.1,0-.2,0l-2.1-1.2c-.2-.1-.2-.3-.1-.5.1-.2.3-.2.5-.1l2.1,1.2c.2.1.2.3.1.5,0,.1-.2.2-.3.2Z",
        }),
        h("path", {
          ...dashed,
          d: "M10.8,11.3c0,0-.1,0-.2,0l-1.1-.6-1.1.6c-.2,0-.4,0-.5-.1-.1-.2,0-.4.1-.5l1.3-.7c.1,0,.3,0,.4,0l1.3.7c.2.1.2.3.1.5,0,.1-.2.2-.3.2Z",
        }),
        h("path", {
          ...dashed,
          d: "M3.9,13.6c-.1,0-.3,0-.3-.2-.1-.2,0-.4.1-.5l2.1-1.2c.2-.1.4,0,.5.1.1.2,0,.4-.1.5l-2.1,1.2c0,0-.1,0-.2,0Z",
        }),
        h("path", {
          ...dashed,
          d: "M.5,15.5c-.1,0-.3,0-.3-.2-.1-.2,0-.4.1-.5l1.3-.7c.2-.1.4,0,.5.1.1.2,0,.4-.1.5l-1.3.7c0,0-.1,0-.2,0Z",
        }),
        // Solid structure (inherits currentColor = --neutral-12)
        h("path", {
          d: "M9.5,21c-.4,0-.9-.1-1.3-.3l-7-4C.5,16.2,0,15.4,0,14.5V6.5c0-.9.5-1.7,1.2-2.2L8.3.3c.8-.4,1.7-.4,2.5,0l7,4c.8.4,1.3,1.3,1.3,2.2h0v8c0,.9-.5,1.7-1.2,2.2l-7,4c-.4.2-.8.3-1.2.3ZM9.5,1c-.3,0-.5,0-.7.2L1.7,5.2c-.5.3-.7.8-.7,1.3v8c0,.5.3,1,.8,1.3l7,4c.5.3,1,.3,1.5,0l7-4c.5-.3.7-.8.7-1.3v-8c0-.5-.3-1-.8-1.3l-7-4c-.2-.1-.5-.2-.8-.2Z",
        }),
        h("path", {
          d: "M9.5,11c0,0-.2,0-.2,0L.6,5.9c-.2-.1-.3-.4-.2-.7.1-.2.4-.3.7-.2l8.5,4.9,8.5-4.9c.2-.1.5,0,.7.2.1.2,0,.5-.2.7l-8.7,5c0,0-.2,0-.2,0Z",
        }),
        h("path", {
          d: "M9.5,21c-.3,0-.5-.2-.5-.5v-10c0-.3.2-.5.5-.5s.5.2.5.5v10c0,.3-.2.5-.5.5Z",
        }),
      ],
    );
  },
});

export const CrossSectionIcon = defineComponent({
  name: "CrossSectionIcon",
  props: {
    active: { type: Boolean, default: false },
  },
  render() {
    const a = this.active;
    const planeFill = a ? "var(--accent-9)" : "var(--neutral-7)";
    const hatchFill = a ? "var(--accent-11)" : "var(--neutral-9)";
    const edge = { fill: "var(--neutral-10)" };
    return h(
      "svg",
      { xmlns: "http://www.w3.org/2000/svg", viewBox: "0 0 15.7 19.5" },
      [
        h("path", {
          fill: planeFill,
          d: "M15.5,14.2s.2-10.4,0-10.6l-9.1,5.1v10.6c.2,0,8.4-4.8,9-5.1Z",
        }),
        h("path", {
          fill: "var(--neutral-10)",
          d: "M15,3.9l-4.8-2.7c-.2-.1-.5-.2-.8-.2s-.5,0-.7.2L1.7,5.2c-.2.1-.3.3-.5.5l5.4,3.1L15,3.9Z",
        }),
        h("path", {
          fill: "var(--neutral-9)",
          d: "M6.1,8.4L1.3,5.7c-.2.2-.3.5-.3.8v8c0,.5.3,1,.8,1.3l4.4,2.5v-9.9Z",
        }),
        h("path", {
          fill: hatchFill,
          d: "M6.4,15.8c0,0-.1,0-.1,0-.1,0-.1-.2,0-.3L15.1,3.4c0-.1.2-.1.3,0,.1,0,.1.2,0,.3L6.6,15.7c0,0-.1.1-.2.1Z",
        }),
        h("path", {
          fill: hatchFill,
          d: "M6.5,18.9c0,0-.1,0-.1,0-.1,0-.1-.2,0-.3L15.2,6.6c0-.1.2-.1.3,0s.1.2,0,.3l-8.9,11.9c0,0-.1.1-.2.1Z",
        }),
        h("path", {
          fill: hatchFill,
          d: "M10.1,17.5c0,0-.1,0-.1,0-.1,0-.1-.2,0-.3l5.3-7.1c0-.1.2-.1.3,0s.1.2,0,.3l-5.3,7.1c0,0-.1.1-.2.1Z",
        }),
        h("path", {
          fill: hatchFill,
          d: "M6.2,12.5c0,0-.1,0-.1,0-.1,0-.1-.2,0-.3l4.6-6.2c0-.1.2-.1.3,0s.1.2,0,.3l-4.6,6.2c0,0-.1.1-.2.1Z",
        }),
        h("path", {
          fill: hatchFill,
          d: "M14.2,15.1c0,0-.1,0-.1,0-.1,0-.1-.2,0-.3l1.1-1.5c0-.1.2-.1.3,0,.1,0,.1.2,0,.3l-1.1,1.5c0,0-.1.1-.2.1Z",
        }),
        h("path", {
          ...edge,
          d: "M15.4,14.6c-.2,0-.4-.2-.4-.4V3.3c0-.2.2-.4.4-.4s.4.2.4.4v10.9c0,.2-.2.4-.4.4Z",
        }),
        h("path", {
          ...edge,
          d: "M6.4,19.5c-.1,0-.3,0-.3-.2,0-.2,0-.4.1-.5l9-4.9c.2,0,.4,0,.5.1,0,.2,0,.4-.1.5l-9,4.9c0,0-.1,0-.2,0Z",
        }),
        h("path", {
          fill: "var(--neutral-12)",
          d: "M15,3.6l.5-.5L10.8.3c-.8-.4-1.7-.4-2.5,0L1.2,4.3C.5,4.8,0,5.6,0,6.5v8c0,.9.5,1.7,1.3,2.2l4.9,2.8.2-.5h.2s0-.5,0-.5l-4.8-2.7c-.5-.3-.7-.8-.8-1.3v-8c0-.5.3-1,.7-1.3L8.8,1.2c.2-.1.5-.2.7-.2s.5,0,.8.2l4.8,2.7.4-.2h-.4Z",
        }),
        h("path", {
          ...edge,
          d: "M6.3,19.5c-.2,0-.4-.2-.4-.4v-10.6c0-.2.2-.4.4-.4s.4.2.4.4v10.6c0,.2-.2.4-.4.4Z",
        }),
        h("path", {
          ...edge,
          d: "M6.1,9c0,0-.1,0-.2,0L1.3,6.2c-.2-.1-.2-.3-.1-.5.1-.2.3-.2.5-.1l4.5,2.6L15.2,3c.2-.1.4,0,.5.1.1.2,0,.4-.1.5l-9.2,5.3c0,0-.1,0-.2,0Z",
        }),
      ],
    );
  },
});

export const ToggleSectionIcon = defineComponent({
  name: "ToggleSectionIcon",
  props: {
    active: { type: Boolean, default: false },
  },
  render() {
    const a = this.active;
    const planeFill = a ? "var(--accent-9)" : "var(--neutral-7)";
    const lineStroke = a ? "var(--accent-11)" : "var(--neutral-9)";
    const cutLine = {
      fill: "none",
      stroke: lineStroke,
      "stroke-linecap": "round",
      "stroke-linejoin": "round",
      "stroke-width": ".8",
    };
    return h(
      "svg",
      { xmlns: "http://www.w3.org/2000/svg", viewBox: "0 0 19 23.7" },
      [
        h("polygon", {
          points: ".4 16.6 9.6 21.7 9.6 11.6 .9 6.6 .4 16.6",
          fill: "var(--neutral-9)",
        }),
        h("polygon", {
          points: "9.6 21.4 9.6 11.6 18.1 6.7 18.3 16.7 9.6 21.4",
          fill: "var(--neutral-6)",
        }),
        h("polygon", {
          points: ".9 6.6 9.6 11.6 18.1 6.7 9.8 1.6 .9 6.6",
          fill: "var(--neutral-10)",
        }),
        h("path", {
          d: "M18.5,7.7c0-.7-.4-1.4-1-1.7L10.5,2c-.6-.4-1.4-.4-2,0L1.5,6c-.6.4-1,1-1,1.7v8c0,.7.4,1.4,1,1.7l7,4c.6.4,1.4.4,2,0l7-4c.6-.4,1-1,1-1.7V7.7Z",
          fill: "none",
          stroke: "var(--neutral-12)",
          "stroke-linecap": "round",
          "stroke-linejoin": "round",
        }),
        h("polygon", {
          points:
            "7.2 21.2 7.3 10.1 16.6 4.8 17.9 5.6 17.9 0 4 8 3.7 23.7 8 21.7 7.2 21.2",
          fill: planeFill,
          opacity: ".5",
        }),
        h("path", { ...cutLine, d: "M7,10.1l9.1-5.2" }),
        h("path", { ...cutLine, d: "M7,20.7v-10.6" }),
      ],
    );
  },
});

/** Camera icons for the projection picker (outline style, sized like the lucide icons). */
const projectionSvg = (size: number, children: Parameters<typeof h>[2]) =>
  h(
    "svg",
    {
      xmlns: "http://www.w3.org/2000/svg",
      viewBox: "0 0 24 24",
      width: size,
      height: size,
      fill: "none",
      stroke: "currentColor",
      "stroke-width": 2,
      "stroke-linecap": "round",
      "stroke-linejoin": "round",
    },
    children,
  );

/** Perspective: the far square is smaller and the edges converge. */
export const PerspectiveIcon = defineComponent({
  name: "PerspectiveIcon",
  props: { size: { type: Number, default: 16 } },
  render() {
    return projectionSvg(this.size, [
      h("rect", { x: 3, y: 3, width: 18, height: 18, rx: 1 }),
      h("rect", { x: 9, y: 9, width: 6, height: 6 }),
      h("path", { d: "M3 3l6 6M21 3l-6 6M21 21l-6-6M3 21l6-6" }),
    ]);
  },
});

/** Orthographic: a cube whose parallel edges stay parallel. */
export const OrthographicIcon = defineComponent({
  name: "OrthographicIcon",
  props: { size: { type: Number, default: 16 } },
  render() {
    return projectionSvg(this.size, [
      h("path", { d: "M3 9h12v12H3z" }),
      h("path", { d: "M9 3h12v12h-6M3 9l6-6M15 9l6-6M15 21l6-6" }),
    ]);
  },
});

export const XrayIcon = defineComponent({
  name: "XrayIcon",
  render() {
    const face = { fill: "var(--neutral-7)" };
    return h(
      "svg",
      { xmlns: "http://www.w3.org/2000/svg", viewBox: "0 0 19 21" },
      [
        // Transparent faces
        h("path", {
          ...face,
          d: "M1,14.5c0,.5.3,1,.8,1.3l7,4c.2.1.5.2.8.2v-9.5L1.2,5.7c-.1.2-.2.5-.2.8v8Z",
        }),
        h("path", {
          ...face,
          d: "M10.3,1.2c-.2-.1-.5-.2-.8-.2s-.5,0-.7.2L1.7,5.2c-.2.1-.4.3-.5.5l8.3,4.8,8.3-4.8c-.1-.2-.3-.4-.5-.5l-7-4Z",
        }),
        h("path", {
          ...face,
          d: "M9.5,20c.3,0,.5,0,.8-.2l7-4c.5-.3.7-.8.7-1.3v-8c0-.3,0-.5-.2-.8l-8.3,4.8v9.5Z",
        }),
        // Back inner edges
        h("path", {
          fill: "var(--neutral-9)",
          d: "M18.6,15l-8.7-4.9V.7c0-.2-.2-.4-.4-.4s-.4.2-.4.4v9.4L.3,15c-.2.1-.2.3-.1.5,0,.1.2.2.3.2s.1,0,.2,0l8.7-4.8s0,0,.1,0,0,0,.1,0l8.6,4.8c.2.1.4,0,.5-.1.1-.2,0-.4-.1-.5Z",
        }),
        // Front inner edges
        h("path", {
          fill: "var(--neutral-10)",
          d: "M18.5,5.3c-.1-.2-.3-.2-.5-.1l-8.5,4.9L1,5.2c-.2-.1-.4,0-.5.1s0,.4.1.5l8.5,4.9v9.8c0,.2.2.4.4.4s.4-.2.4-.4v-9.8l8.5-4.9c.2-.1.2-.3.1-.5Z",
        }),
        // Outline (inherits currentColor = --neutral-12)
        h("path", {
          d: "M9.5,21c-.4,0-.9-.1-1.2-.3l-7-4c-.8-.4-1.3-1.3-1.3-2.2V6.5c0-.9.5-1.7,1.2-2.2L8.3.3c.8-.4,1.7-.4,2.5,0l7,4c.8.4,1.3,1.3,1.3,2.2h0v8c0,.9-.5,1.7-1.2,2.2l-7,4c-.4.2-.8.3-1.2.3ZM9.5,1c-.3,0-.5,0-.7.2L1.7,5.2c-.5.3-.7.8-.7,1.3v8c0,.5.3,1,.8,1.3l7,4c.5.3,1,.3,1.5,0l7-4c.5-.3.7-.8.7-1.3v-8c0-.5-.3-1-.8-1.3l-7-4c-.2-.1-.5-.2-.8-.2Z",
        }),
      ],
    );
  },
});
