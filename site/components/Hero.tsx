"use client";

import { useRef } from "react";
import gsap from "gsap";
import { useGSAP } from "@gsap/react";
import Mascot from "./Mascot";
import { REPO_URL } from "@/lib/content";
import { scoutReact } from "@/lib/scout";

gsap.registerPlugin(useGSAP);

// The exact lines `mergemind preflight` prints on a ready repository.
const PREFLIGHT = [
  "token can write issues, comments and branches (push)",
  "token can invite and remove collaborators (admin / Administration: write)",
  "`main` is protected",
  "mergemind-verify workflow is on the default branch (runs PR tests without secrets)",
  "policy file found: autonomy=suggest, access.mode=collaborator",
];

export default function Hero() {
  const root = useRef<HTMLElement>(null);

  useGSAP(
    () => {
      const mm = gsap.matchMedia();
      mm.add("(prefers-reduced-motion: no-preference)", () => {
        const st = gsap.utils.toArray<HTMLElement>(".readout .st");
        const ready = root.current!.querySelector<HTMLElement>(".ready")!;
        const tl = gsap.timeline({ delay: 0.15 });

        tl.from(".stamp span", { yPercent: 35, skewY: 5, scale: 1.1, duration: 0.55, stagger: 0.12, ease: "power4.out" })
          .from(".unit", { y: 24, rotate: 1.5, duration: 0.6, ease: "back.out(1.6)" }, 0.1)
          .call(() => {
            scoutReact("sleepy", 0);
            st.forEach((el) => {
              el.textContent = "····";
              el.classList.add("pending");
            });
            ready.style.visibility = "hidden";
          }, undefined, 0.2);

        st.forEach((el) => {
          tl.call(() => {
            el.textContent = "PASS";
            el.classList.remove("pending");
          }, undefined, "+=0.32").fromTo(el, { color: "#ffffff" }, { color: "", duration: 0.4, clearProps: "color" }, "<");
        });

        tl.call(() => {
          ready.style.visibility = "";
          scoutReact("delighted", 1400);
        }, undefined, "+=0.3");
      });
      return () => mm.revert();
    },
    { scope: root },
  );

  return (
    <section className="hero" ref={root}>
      <div className="wrap">
        <div className="hero-copy">
          <p className="eyebrow">
            Field manual · <b>MergeMind 0.1</b> · for repository maintainers
          </p>
          <h1 className="stamp" aria-label="The person who found the bug fixes it.">
            <span>The person</span>
            <span>who found the bug</span>
            <span>
              <em>fixes it.</em>
            </span>
          </h1>
          <p className="lede">
            MergeMind is a GitHub agent. It investigates new issues in your code, gives the reporter time-limited access
            and a briefing, checks their pull request, and merges it when a maintainer says yes. Nothing it posts can
            leak a secret.
          </p>
          <div className="cta">
            <a className="btn btn-primary" href="#setup">
              Start the setup
            </a>
            <a className="btn" href={REPO_URL} target="_blank" rel="noreferrer">
              Source on GitHub
            </a>
          </div>
          <dl className="specs">
            <div>
              <dt>Runs on</dt>
              <dd>GitHub Actions, no server</dd>
            </div>
            <div>
              <dt>Model cost per issue</dt>
              <dd>≈ $0.007 · $0 on free tier</dd>
            </div>
            <div>
              <dt>Fix file in top 3</dt>
              <dd>74% of 450 real issues</dd>
            </div>
            <div>
              <dt>Leaks in the test corpus</dt>
              <dd>0</dd>
            </div>
          </dl>
        </div>

        <div className="unit">
          <div className="plate-head">
            <span>
              Unit <b>Scout</b>
            </span>
            <span>S/N MM-0001</span>
          </div>
          <div className="unit-body rivets">
            <Mascot size={200} />
            <p className="bubble">
              I follow your cursor. Poke me if you like. Poke me four times and see what happens.
            </p>
          </div>
          <pre className="readout" aria-label="Example preflight output">
            <span className="prompt">$ mergemind preflight --repo you/your-repo</span>
            {"\n"}
            {PREFLIGHT.map((line) => (
              <span key={line} className="line">
                <span className="st">PASS</span>
                <span>{line}</span>
              </span>
            ))}
            {"\n"}
            <span className="ready">ready</span>
          </pre>
        </div>
      </div>
    </section>
  );
}
