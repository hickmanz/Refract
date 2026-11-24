// The room light of a photoreal picture, in lux (illuminance on a surface that faces the camera). A slider position of 0 is a dark
// room; 1..100 run on a log scale from 1 lux to 10,000 lux (a moonlit night to bright daylight in the shade).

export const ROOM_LUX_STEPS = 100;
export const ROOM_LUX_MIN = 1;
export const ROOM_LUX_MAX = 10000;
export const DEFAULT_ROOM_LUX = 500;

/** How a picture is exposed: a camera with fixed settings, a camera that meters the room (a white card is mid-grey whatever the
 *  room light) or one that meters the lit exit surfaces (they come out mid-bright whatever their level). */
export type ExposureMode = "fixed" | "room" | "surfaces";
export const EXPOSURE_MODES: { id: ExposureMode; label: string; hint: string }[] = [
  { id: "fixed", label: "Fixed camera", hint: "Same exposure whatever the room light or the LED level: a dim room looks dim, a bright one burns out." },
  { id: "room", label: "Follow the room light", hint: "Like a camera that meters the room: a white card in the room is always mid-grey, so the LED glow is shown against the room." },
  { id: "surfaces", label: "Follow the lit surfaces", hint: "Like a camera that meters the LED surface: it always looks mid-bright, so the room goes dark when the LED is bright." },
];

/** Quick picks for the room light. The values are typical illuminances on a desk or table. */
export const ROOM_LUX_PRESETS: { label: string; lux: number; hint: string }[] = [
  { label: "Dark", lux: 0, hint: "A dark room: only the LEDs light the scene" },
  { label: "Dim", lux: 50, hint: "About 50 lux: a dim living room or hallway at night" },
  { label: "Home", lux: 150, hint: "About 150 lux: a lamp-lit living room" },
  { label: "Office", lux: 500, hint: "About 500 lux: a well-lit office or lab" },
  { label: "Bright", lux: 1000, hint: "About 1,000 lux: a bright desk lamp, or a room by a window" },
  { label: "Daylight", lux: 10000, hint: "About 10,000 lux: outdoors in the shade, overcast daylight" },
];

export function luxFromPos(pos: number): number {
  if (pos <= 0) return 0;
  const t = (Math.min(pos, ROOM_LUX_STEPS) - 1) / (ROOM_LUX_STEPS - 1);
  const lux = ROOM_LUX_MIN * Math.pow(ROOM_LUX_MAX / ROOM_LUX_MIN, t);
  return lux < 10 ? Math.round(lux * 10) / 10 : lux < 100 ? Math.round(lux) : Math.round(lux / 10) * 10;
}

export function posFromLux(lux: number): number {
  if (lux <= 0) return 0;
  const t = Math.log(Math.max(lux, ROOM_LUX_MIN) / ROOM_LUX_MIN) / Math.log(ROOM_LUX_MAX / ROOM_LUX_MIN);
  return Math.min(ROOM_LUX_STEPS, Math.max(1, Math.round(1 + t * (ROOM_LUX_STEPS - 1))));
}

export function fmtLux(lux: number): string {
  return lux <= 0 ? "Dark room" : `${lux >= 1000 ? (lux / 1000).toFixed(lux >= 10000 ? 0 : 1) + "k" : lux} lux`;
}

/** cd/m2 with a sensible number of digits (the pipe exit is thousands, a table tens). */
export function fmtLum(v: number): string {
  if (!(v > 0)) return "0";
  if (v >= 100000) return `${Math.round(v / 1000)}k`;
  if (v >= 1000) return `${(v / 1000).toFixed(1)}k`;
  return v >= 100 ? `${Math.round(v)}` : v >= 10 ? v.toFixed(1) : v.toFixed(2);
}
