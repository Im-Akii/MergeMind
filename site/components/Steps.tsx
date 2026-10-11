"use client";

import CodeBlock from "./CodeBlock";
import PunchCard from "./PunchCard";
import { useGuide } from "./Guide";
import { steps } from "@/lib/content";

export default function Steps() {
  const { done, toggle, current } = useGuide();

  return (
    <ol className="steps">
      {steps.map((s, i) => {
        const isDone = done.includes(s.id);
        return (
          <li key={s.id} id={`step-${s.id}`} className={`step${isDone ? " done" : ""}${current === i ? " current" : ""}`}>
            <span className="step-no" aria-hidden="true">
              {String(i + 1).padStart(2, "0")}
            </span>
            <div className="step-body">
              <div className="step-title">
                <h3 tabIndex={-1}>
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
