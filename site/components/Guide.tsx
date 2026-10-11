"use client";

// Scout's sense of where the reader is: which section and setup step are on screen, which steps
// are done, and what to say about it. The side rail, the steps and the phone dock all read this.
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { steps } from "@/lib/content";
import { scoutLook, scoutReact } from "@/lib/scout";

const KEY = "mergemind-setup-done";

export const SECTIONS = [
  { id: "setup", label: "Setup", line: "" },
  { id: "autonomy", label: "Autonomy", line: "Start me on suggest. Turn the dial to see what I'm allowed to do at each level." },
  { id: "config", label: "Policy file", line: "Every key has a default, so an empty file works. Change only what you need." },
  { id: "commands", label: "Commands", line: "A comment that starts with a slash is for me. Maintainers get the powerful ones." },
  { id: "env", label: "Environment", line: "Keys go in the mergemind environment, never in the repository." },
  { id: "faults", label: "Fault codes", line: "Find the line you're seeing on the left. The fix is on the right." },
  { id: "safety", label: "Safety model", line: "I read your code, but I can't write to GitHub except through the policy gate." },
  { id: "bench", label: "On the bench", line: "Things I'd like to learn next. None of them are built yet." },
] as const;

export type Say = {
  label: string;
  text: string;
  action?: { label: string; run: () => void };
};

type Guide = {
  done: string[];
  section: string;
  current: number | null; // index of the step on screen
  inManual: boolean;
  toggle: (id: string) => void;
  goTo: (index: number) => void;
  say: Say;
};

const Ctx = createContext<Guide | null>(null);

export function useGuide(): Guide {
  const g = useContext(Ctx);
  if (!g) throw new Error("useGuide outside GuideProvider");
  return g;
}

export function GuideProvider({ children }: { children: React.ReactNode }) {
  const [done, setDone] = useState<string[]>([]);
  const [section, setSection] = useState<string>("setup");
  const [current, setCurrent] = useState<number | null>(null);
  const [inManual, setInManual] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    try {
      const saved = JSON.parse(window.localStorage.getItem(KEY) || "[]");
      if (Array.isArray(saved)) setDone(saved.filter((id) => steps.some((s) => s.id === id)));
    } catch {
      /* nothing saved, or storage blocked */
    }
  }, []);

  // what's on screen: whatever crosses a reading line 40% down the viewport
  useEffect(() => {
    const secEls = SECTIONS.map((s) => document.getElementById(s.id));
    const stepEls = steps.map((s) => document.getElementById(`step-${s.id}`));
    const crossing = (els: (HTMLElement | null)[], y: number) =>
      els.findIndex((el) => {
        const r = el?.getBoundingClientRect();
        return !!r && r.top <= y && r.bottom > y;
      });
    let raf = 0;
    const read = () => {
      raf = 0;
      const y = window.innerHeight * 0.4;
      const s = crossing(secEls, y);
      if (s !== -1) setSection(SECTIONS[s].id);
      // the last step whose top has passed the line, so the gaps between steps don't count as "no step"
      let i = -1;
      stepEls.forEach((el, k) => {
        if (el && el.getBoundingClientRect().top <= y) i = k;
      });
      const last = stepEls[stepEls.length - 1];
      if (i === stepEls.length - 1 && last && last.getBoundingClientRect().bottom < y) i = -1;
      setCurrent(i === -1 ? null : i);
      const r = box.current?.getBoundingClientRect();
      setInManual(!!r && r.top < window.innerHeight * 0.6 && r.bottom > window.innerHeight * 0.4);
    };
    const onScroll = () => {
      if (!raf) raf = window.requestAnimationFrame(read);
    };
    read();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
      window.cancelAnimationFrame(raf);
    };
  }, []);

  // glance at the content whenever the reader reaches a new step
  useEffect(() => {
    if (current !== null) scoutLook(window.matchMedia("(max-width: 960px)").matches ? "up" : "right", 1300);
  }, [current]);

  const save = (next: string[]) => {
    setDone(next);
    try {
      window.localStorage.setItem(KEY, JSON.stringify(next));
    } catch {
      /* private window: progress just isn't remembered */
    }
  };

  const goTo = useCallback((i: number) => {
    const el = document.getElementById(`step-${steps[i].id}`);
    if (!el) return;
    const smooth = !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    el.scrollIntoView({ behavior: smooth ? "smooth" : "auto", block: "start" });
    el.querySelector<HTMLElement>("h3")?.focus({ preventScroll: true });
  }, []);

  const toggle = useCallback(
    (id: string) => {
      const next = done.includes(id) ? done.filter((d) => d !== id) : [...done, id];
      save(next);
      if (next.length > done.length) scoutReact(next.length === steps.length ? "sparkle" : "heart", 1300);
    },
    [done],
  );

  const firstOpen = steps.findIndex((s) => !done.includes(s.id));
  const allDone = firstOpen === -1;

  const say = useMemo<Say>(() => {
    const resume = (verb: string) =>
      allDone ? undefined : { label: `${verb} step ${firstOpen + 1}`, run: () => goTo(firstOpen) };

    if (section === "setup" && current !== null) {
      const s = steps[current];
      const isDone = done.includes(s.id);
      const after = steps.findIndex((x, i) => i > current && !done.includes(x.id));
      const label = `Step ${current + 1} of ${steps.length} · ${s.title}`;
      if (allDone) return { label: "Setup complete", text: "All nine done. Open an issue from your second account and watch me work." };
      if (!isDone)
        return {
          label,
          text: s.tip,
          action: {
            label: after === -1 && firstOpen === current ? "Done, that's the last one" : "Done, what's next?",
            run: () => {
              toggle(s.id);
              const n = after !== -1 ? after : firstOpen !== current ? firstOpen : -1;
              if (n !== -1) window.setTimeout(() => goTo(n), 250);
            },
          },
        };
      return {
        label,
        text: "This one's done. " + (after !== -1 ? `Next up is ${steps[after].title.toLowerCase()}.` : "Only earlier steps are left."),
        action: after !== -1 ? { label: `Go to step ${after + 1}`, run: () => goTo(after) } : resume("Back to"),
      };
    }
    if (section === "setup") {
      if (allDone) return { label: "Setup complete", text: "All nine done. Open an issue from your second account and watch me work." };
      return done.length === 0
        ? { label: "Before you start", text: "Get the parts on this list together first. Then I'll take you through nine steps, about twenty minutes in all.", action: resume("Start") }
        : { label: "Welcome back", text: `You've done ${done.length} of ${steps.length}. Let's carry on where you stopped.`, action: resume("Continue at") };
    }
    const sec = SECTIONS.find((x) => x.id === section);
    return { label: sec?.label ?? "", text: sec?.line ?? "", action: done.length > 0 ? resume("Back to") : undefined };
  }, [section, current, done, allDone, firstOpen, goTo, toggle]);

  const value = useMemo(() => ({ done, section, current, inManual, toggle, goTo, say }), [done, section, current, inManual, toggle, goTo, say]);

  return (
    <Ctx.Provider value={value}>
      <div ref={box}>{children}</div>
    </Ctx.Provider>
  );
}
