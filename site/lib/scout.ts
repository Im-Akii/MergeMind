// A tiny event bus so any part of the page can make Scout react or talk.

export const BASE = process.env.NEXT_PUBLIC_BASE_PATH || "";
export const SPRITES = {
  directions: `${BASE}/mascot/scout-directions.webp`,
  reactions: `${BASE}/mascot/scout-reactions.webp`,
};

// Cell order in the 3x3 sprite sheets (from page-mascot).
export const DIRECTIONS = ["up-left", "up", "up-right", "left", "center", "right", "down-left", "down", "down-right"] as const;
export const REACTIONS = ["blink", "heart", "sparkle", "surprised", "wink", "bashful", "sleepy", "dizzy", "delighted"] as const;
export type Direction = (typeof DIRECTIONS)[number];
export type Reaction = (typeof REACTIONS)[number];

export function cell(i: number): string {
  return `${(i % 3) * 50}% ${Math.floor(i / 3) * 50}%`;
}

export function scoutReact(mood: Reaction | null, ms = 900): void {
  window.dispatchEvent(new CustomEvent("scout:react", { detail: { mood, ms } }));
}

export function scoutLook(dir: Direction, ms = 1400): void {
  window.dispatchEvent(new CustomEvent("scout:look", { detail: { dir, ms } }));
}
