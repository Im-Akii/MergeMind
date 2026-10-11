"use client";

import { useEffect, useState } from "react";
import CodeBlock from "./CodeBlock";
import PunchCard from "./PunchCard";
import { steps } from "@/lib/content";
import { scoutReact } from "@/lib/scout";

const KEY = "mergemind-setup-done";

export function readDone(): string[] {
  try {
    return JSON.parse(window.localStorage.getItem(KEY) || "[]");
  } catch {
    return [];
  }
}

export default function Steps() {
  const [done, setDone] = useState<string[]>([]);

  useEffect(() => setDone(readDone()), []);

  const toggle = (id: string) => {
    const next = done.includes(id) ? done.filter((d) => d !== id) : [...done, id];
    setDone(next);
    try {
      window.localStorage.setItem(KEY, JSON.stringify(next));
    } catch {
      /* private window: progress just isn't remembered */
    }
    window.dispatchEvent(new CustomEvent("setup:progress", { detail: next.length }));
    if (next.length > done.length) scoutReact(next.length === steps.length ? "sparkle" : "heart", 1200);
  };

  return (
    <ol className="steps">
      {steps.map((s, i) => {
        const isDone = done.includes(s.id);
        return (
          <li key={s.id} id={`step-${s.id}`} className={`step${isDone ? " done" : ""}`}>
            <span className="step-no" aria-hidden="true">
              {String(i + 1).padStart(2, "0")}
            </span>
            <div className="step-body">
              <div className="step-title">
                <h3>
                  <span className="sr-only">Step {i + 1}: </span>
                  {s.title}
                </h3>
                <button type="button" className="donebtn" aria-pressed={isDone} onClick={() => toggle(s.id)}>
                  {isDone ? "Done" : "Mark done"}
                </button>
              </div>
              <p>{s.lead}</p>
              {s.list && (
                <ul>
                  {s.list.map((l) => (
                    <li key={l}>{l}</li>
                  ))}
                </ul>
              )}
              {s.perms && <PunchCard />}
              {s.code?.map((c) => <CodeBlock key={c.label} label={c.label} text={c.text} />)}
              {s.note && <p className="note">{s.note}</p>}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
