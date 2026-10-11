"use client";

// Phones: the side rail is hidden, so Scout docks at the bottom of the screen while you're in the
// manual. Tap Scout to tuck the bubble away or bring it back.
import { useState } from "react";
import Mascot from "./Mascot";
import Speech from "./Speech";
import { useGuide } from "./Guide";
import { steps } from "@/lib/content";

export default function Dock() {
  const { say, done, inManual } = useGuide();
  const [open, setOpen] = useState(true);

  return (
    <div className={`dock${inManual ? " show" : ""}${open ? "" : " tucked"}`} inert={!inManual}>
      <div className="dock-inner">
        <div className="dock-scout">
          <Mascot size={64} />
          <button type="button" className="dock-toggle" onClick={() => setOpen(!open)} aria-expanded={open}>
            {open ? "Hide" : "Scout"}
          </button>
        </div>
        {open && (
          <div className="dock-talk">
            <Speech say={say} />
            <span className="dock-count">
              {done.length}/{steps.length}
            </span>
          </div>
        )}
      </div>
    </div>
  );
}
