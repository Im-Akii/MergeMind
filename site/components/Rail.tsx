"use client";

// Desktop: Scout rides down the page beside the manual, talking you through each step.
import Mascot from "./Mascot";
import Speech from "./Speech";
import { SECTIONS, useGuide } from "./Guide";
import { steps } from "@/lib/content";

export default function Rail() {
  const { say, done, section, current, goTo } = useGuide();

  return (
    <aside className="rail" aria-label="Scout, your guide">
      <div className="guide">
        <Mascot size={132} />
        <Speech say={say} className="bubble" />
        <div className="meter">
          <div className="meter-label">
            <span>Setup</span>
            <span>
              {done.length} / {steps.length}
            </span>
          </div>
          <div className="meter-bar" aria-hidden="true">
            {steps.map((s, i) => (
              <i key={s.id} className={`${done.includes(s.id) ? "on" : ""}${current === i ? " here" : ""}`} />
            ))}
          </div>
        </div>
      </div>
      <nav aria-label="On this page">
        <ul className="toc">
          {SECTIONS.map((s) => (
            <li key={s.id}>
              <a href={`#${s.id}`} className={section === s.id ? "active" : ""}>
                {s.label}
              </a>
              {s.id === "setup" && (
                <ol className="toc-steps">
                  {steps.map((st, i) => (
                    <li key={st.id}>
                      <button
                        type="button"
                        className={`${current === i ? "here" : ""}${done.includes(st.id) ? " done" : ""}`}
                        onClick={() => goTo(i)}
                      >
                        <span className="tick" aria-hidden="true" />
                        <span className="sr-only">{done.includes(st.id) ? "Done: " : ""}</span>
                        {st.title}
                      </button>
                    </li>
                  ))}
                </ol>
              )}
            </li>
          ))}
        </ul>
      </nav>
    </aside>
  );
}
