"use client";

// Scout's speech bubble. New lines type out; the full text sits underneath (invisible) so the
// bubble keeps its size, and screen readers get the whole sentence at once.
import { useEffect, useState } from "react";
import gsap from "gsap";
import type { Say } from "./Guide";

export default function Speech({ say, className = "" }: { say: Say; className?: string }) {
  const [shown, setShown] = useState(say.text);

  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setShown(say.text);
      return;
    }
    const counter = { n: 0 };
    setShown("");
    const tw = gsap.to(counter, {
      n: say.text.length,
      duration: Math.min(1.6, 0.25 + say.text.length * 0.014),
      ease: "none",
      onUpdate: () => setShown(say.text.slice(0, Math.round(counter.n))),
    });
    return () => {
      tw.kill();
    };
  }, [say.text]);

  const typing = shown.length < say.text.length;
  return (
    <div className={`speech ${className}`}>
      {say.label && <p className="speech-label">{say.label}</p>}
      <p className="speech-text" aria-hidden="true">
        <span className="speech-ghost">{say.text}</span>
        <span className="speech-typed">
          {shown}
          {typing && <span className="caret" />}
        </span>
      </p>
      <p className="sr-only" aria-live="polite">
        {say.text}
      </p>
      {say.action && (
        <button type="button" className="speech-btn" onClick={say.action.run}>
          {say.action.label}
          <span aria-hidden="true"> →</span>
        </button>
      )}
    </div>
  );
}
