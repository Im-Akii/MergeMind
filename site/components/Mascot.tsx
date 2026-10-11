"use client";

// Scout follows the cursor in eight directions and reacts when poked.
// Behaviour follows page-mascot (MIT, Kamran Ahmed), rewritten without its React package.
import { useEffect, useRef, useState } from "react";
import gsap from "gsap";
import { DIRECTIONS, REACTIONS, SPRITES, cell, type Direction, type Reaction } from "@/lib/scout";

const RING: Direction[] = ["right", "down-right", "down", "down-left", "left", "up-left", "up", "up-right"];
const SLICE = (Math.PI * 2) / RING.length;
const HYSTERESIS = 0.12;
const DEAD_ZONE = 70;
const POKE_MOODS: Reaction[] = ["heart", "sparkle", "delighted"];
const POKE_WINDOW = 1600;
const DIZZY_AFTER = 4;
const IDLE_MS = 25000;

const wrap = (a: number) => Math.atan2(Math.sin(a), Math.cos(a));

export default function Mascot({ size = 140, label = "Scout the robot" }: { size?: number; label?: string }) {
  const btn = useRef<HTMLButtonElement>(null);
  const body = useRef<HTMLSpanElement>(null);
  const timers = useRef<number[]>([]);
  const pokes = useRef({ count: 0, at: 0 });
  const [dir, setDir] = useState<Direction>("center");
  const [mood, setMood] = useState<Reaction | null>(null);

  const clear = () => {
    timers.current.forEach(window.clearTimeout);
    timers.current = [];
  };
  const later = (ms: number, m: Reaction | null) => timers.current.push(window.setTimeout(() => setMood(m), ms));

  // follow the cursor (fine pointers only), doze off when nobody moves
  useEffect(() => {
    if (!window.matchMedia("(hover: hover) and (pointer: fine)").matches) return;
    let idx = -1;
    let pt: { x: number; y: number } | null = null;
    let idle = 0;
    let asleep = false;
    const aim = () => {
      const el = btn.current;
      if (!el || !pt) return;
      const r = el.getBoundingClientRect();
      const dx = pt.x - (r.left + r.width / 2);
      const dy = pt.y - (r.top + r.height / 2);
      if (Math.hypot(dx, dy) < DEAD_ZONE) {
        idx = -1;
        setDir("center");
        return;
      }
      const a = Math.atan2(dy, dx);
      if (idx !== -1 && Math.abs(wrap(a - idx * SLICE)) < SLICE / 2 + HYSTERESIS) return;
      idx = (Math.round(a / SLICE) + RING.length) % RING.length;
      setDir(RING[idx]);
    };
    const nap = () => {
      window.clearTimeout(idle);
      if (asleep) {
        asleep = false;
        setMood(null);
      }
      idle = window.setTimeout(() => {
        asleep = true;
        setMood("sleepy");
      }, IDLE_MS);
    };
    const move = (e: PointerEvent) => {
      pt = { x: e.clientX, y: e.clientY };
      aim();
      nap();
    };
    window.addEventListener("pointermove", move, { passive: true });
    window.addEventListener("scroll", aim, { passive: true });
    nap();
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("scroll", aim);
      window.clearTimeout(idle);
    };
  }, []);

  // reactions requested by the rest of the page
  useEffect(() => {
    const on = (e: Event) => {
      const { mood: m, ms } = (e as CustomEvent<{ mood: Reaction | null; ms: number }>).detail;
      clear();
      setMood(m);
      if (m && ms > 0) later(ms, null);
    };
    window.addEventListener("scout:react", on);
    return () => {
      window.removeEventListener("scout:react", on);
      clear();
    };
  }, []);

  const poke = () => {
    clear();
    const now = Date.now();
    const p = pokes.current;
    p.count = now - p.at < POKE_WINDOW ? p.count + 1 : 1;
    p.at = now;
    if (p.count >= DIZZY_AFTER) {
      p.count = 0;
      setMood("dizzy");
      later(1100, null);
    } else {
      setMood("blink");
      later(120, POKE_MOODS[(p.count - 1) % POKE_MOODS.length]);
      later(560, null);
    }
    if (body.current && !window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      gsap.fromTo(
        body.current,
        { scaleX: 1, scaleY: 1 },
        {
          keyframes: [
            { scaleX: 1.1, scaleY: 0.86, duration: 0.08, ease: "power1.out" },
            { scaleX: 0.95, scaleY: 1.08, duration: 0.11, ease: "power1.inOut" },
            { scaleX: 1.03, scaleY: 0.97, duration: 0.11, ease: "power1.inOut" },
            { scaleX: 1, scaleY: 1, duration: 0.12 },
          ],
        },
      );
    }
  };

  return (
    <button ref={btn} type="button" className="mascot" onClick={poke} aria-label={`Poke ${label}`} style={{ width: size, height: size }}>
      <span ref={body} className="mascot-inner">
        <span
          className="sheet"
          style={{ backgroundImage: `url(${SPRITES.directions})`, backgroundPosition: cell(DIRECTIONS.indexOf(dir)), opacity: mood ? 0 : 1 }}
        />
        <span
          className="sheet"
          style={{ backgroundImage: `url(${SPRITES.reactions})`, backgroundPosition: cell(REACTIONS.indexOf(mood ?? "blink")), opacity: mood ? 1 : 0 }}
        />
      </span>
    </button>
  );
}
