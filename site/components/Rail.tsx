"use client";

import { useEffect, useState } from "react";
import Mascot from "./Mascot";
import { readDone } from "./Steps";
import { steps } from "@/lib/content";
import { scoutReact } from "@/lib/scout";

export const SECTIONS = [
  { id: "setup", label: "Setup", line: "Nine steps, about twenty minutes. Tick them off and I'll keep count." },
  { id: "autonomy", label: "Autonomy", line: "Start me on suggest. Turn the dial up once you trust me." },
  { id: "config", label: "Policy file", line: "Every key has a default. An empty file works." },
  { id: "commands", label: "Commands", line: "A comment that starts with a slash is for me." },
  { id: "env", label: "Environment", line: "Keys go in the mergemind environment, never in the repository." },
  { id: "faults", label: "Fault codes", line: "401 means the token value is wrong. Changing permissions won't fix it." },
  { id: "safety", label: "Safety model", line: "I read your code. I can't write to GitHub except through the policy gate." },
  { id: "bench", label: "On the bench", line: "Things I'd like to learn next. None of them are built yet." },
];

export default function Rail() {
  const [active, setActive] = useState(SECTIONS[0].id);
  const [count, setCount] = useState(0);

  useEffect(() => {
    setCount(readDone().length);
    const onProgress = (e: Event) => setCount((e as CustomEvent<number>).detail);
    window.addEventListener("setup:progress", onProgress);

    const io = new IntersectionObserver(
      (entries) => entries.forEach((en) => en.isIntersecting && setActive(en.target.id)),
      { rootMargin: "-35% 0px -60% 0px" },
    );
    SECTIONS.forEach((s) => {
      const el = document.getElementById(s.id);
      if (el) io.observe(el);
    });
    return () => {
      window.removeEventListener("setup:progress", onProgress);
      io.disconnect();
    };
  }, []);

  const total = steps.length;
  const line =
    count === total && active === "setup"
      ? "All nine done. Open an issue from your second account and watch."
      : SECTIONS.find((s) => s.id === active)?.line;

  return (
    <aside className="rail" aria-label="On this page">
      <div className="guide">
        <Mascot size={132} />
        <p className="bubble" aria-live="polite">
          {line}
        </p>
        <div className="meter">
          <div className="meter-label">
            <span>Setup</span>
            <span>
              {count} / {total}
            </span>
          </div>
          <div className="meter-bar" aria-hidden="true">
            {steps.map((s, i) => (
              <i key={s.id} className={i < count ? "on" : ""} />
            ))}
          </div>
        </div>
      </div>
      <ul className="toc">
        {SECTIONS.map((s) => (
          <li key={s.id}>
            <a href={`#${s.id}`} className={active === s.id ? "active" : ""} onClick={() => scoutReact("wink", 700)}>
              {s.label}
            </a>
          </li>
        ))}
      </ul>
    </aside>
  );
}
