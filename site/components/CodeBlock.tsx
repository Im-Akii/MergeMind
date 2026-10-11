"use client";

import { useRef, useState } from "react";

export default function CodeBlock({ label, text }: { label: string; text: string }) {
  const pre = useRef<HTMLPreElement>(null);
  const [state, setState] = useState<"idle" | "copied" | "select">("idle");

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setState("copied");
    } catch {
      // clipboard refused: select the text so Ctrl+C works
      const sel = window.getSelection();
      if (sel && pre.current) {
        const range = document.createRange();
        range.selectNodeContents(pre.current);
        sel.removeAllRanges();
        sel.addRange(range);
      }
      setState("select");
    }
    window.setTimeout(() => setState("idle"), 1600);
  };

  return (
    <div className="code">
      <div className="code-head">
        <span>{label}</span>
        <button type="button" className="copy" onClick={copy}>
          {state === "copied" ? "Copied" : state === "select" ? "Selected" : "Copy"}
        </button>
      </div>
      <pre ref={pre}>{text}</pre>
    </div>
  );
}
