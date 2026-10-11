"use client";

import { useRef, useState } from "react";
import gsap from "gsap";
import { useGSAP } from "@gsap/react";
import { autonomy } from "@/lib/content";
import { scoutReact } from "@/lib/scout";

gsap.registerPlugin(useGSAP);

const ANGLES = [-75, -28, 28, 75];
// label positions on an arc above the knob (dial box is 300 x 210, knob centre at 150,130)
const POS = ANGLES.map((a) => {
  const r = (a * Math.PI) / 180;
  return { left: 150 + Math.sin(r) * 118, top: 130 - Math.cos(r) * 108 };
});

export default function AutonomyDial() {
  const root = useRef<HTMLDivElement>(null);
  const [at, setAt] = useState(1); // suggest is the default

  const { contextSafe } = useGSAP(
    () => {
      gsap.set(".dial-knob", { rotation: ANGLES[1] });
    },
    { scope: root },
  );

  const pick = contextSafe((i: number) => {
    setAt(i);
    scoutReact(autonomy[i].mood, 1600);
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    gsap.to(".dial-knob", { rotation: ANGLES[i], duration: reduce ? 0 : 0.55, ease: "back.out(2.2)" });
    if (!reduce) gsap.fromTo(".dial-read", { x: 8 }, { x: 0, duration: 0.3, ease: "power2.out" });
  });

  const onKey = (e: React.KeyboardEvent) => {
    const d = e.key === "ArrowRight" || e.key === "ArrowUp" ? 1 : e.key === "ArrowLeft" || e.key === "ArrowDown" ? -1 : 0;
    if (!d) return;
    e.preventDefault();
    const next = (at + d + autonomy.length) % autonomy.length;
    pick(next);
    root.current?.querySelectorAll<HTMLButtonElement>(".dial-opt")[next]?.focus();
  };

  const cur = autonomy[at];
  return (
    <div className="dial-box" ref={root}>
      <div className="dial" role="radiogroup" aria-label="Autonomy level" onKeyDown={onKey}>
        <div className="dial-face" aria-hidden="true">
          <div className="dial-knob" />
        </div>
        {autonomy.map((a, i) => (
          <button
            key={a.id}
            type="button"
            role="radio"
            aria-checked={i === at}
            tabIndex={i === at ? 0 : -1}
            className="dial-opt"
            style={{ left: POS[i].left, top: POS[i].top }}
            onClick={() => pick(i)}
          >
            {a.label}
          </button>
        ))}
      </div>
      <div className="dial-read" aria-live="polite">
        <p className="eyebrow">
          autonomy: <b>{cur.id}</b>
          {cur.id === "suggest" ? " · default" : ""}
        </p>
        <h3>{cur.label}</h3>
        <p>{cur.body}</p>
        <p className="kill">
          Kill switches: a maintainer comments <code>/mergemind stop</code> on an issue, or you set{" "}
          <code>MERGEMIND_AUTONOMY=observe</code>. That variable can only lower autonomy, never raise it.
        </p>
      </div>
    </div>
  );
}
