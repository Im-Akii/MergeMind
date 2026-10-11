"use client";

import { useRef } from "react";
import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import { useGSAP } from "@gsap/react";
import { stations } from "@/lib/content";

gsap.registerPlugin(ScrollTrigger, useGSAP);

export default function Conveyor() {
  const root = useRef<HTMLDivElement>(null);

  useGSAP(
    () => {
      const mm = gsap.matchMedia();
      const items = gsap.utils.toArray<HTMLElement>(".station");
      const light = (upTo: number) => items.forEach((el, i) => el.classList.toggle("lit", i <= upTo));

      // desktop: the issue tag rides the belt as you scroll, lighting each station it reaches
      mm.add("(min-width: 961px) and (prefers-reduced-motion: no-preference)", () => {
        const tag = root.current!.querySelector<HTMLElement>(".tag")!;
        const belt = root.current!.querySelector<HTMLElement>(".belt-wrap")!;
        const centre = (i: number) => {
          const s = items[i];
          return s.offsetLeft + s.offsetWidth / 2 - tag.offsetWidth / 2;
        };
        gsap.set(tag, { x: () => centre(0) });
        const tl = gsap.timeline({
          scrollTrigger: {
            trigger: belt,
            start: "top 75%",
            end: "bottom 45%",
            scrub: 0.6,
            invalidateOnRefresh: true,
            onUpdate: (self) => light(Math.floor(self.progress * (items.length - 1) + 0.04)),
          },
        });
        tl.fromTo(tag, { x: () => centre(0) }, { x: () => centre(items.length - 1), ease: "none" }, 0).to(
          ".treads",
          { backgroundPositionX: "-360px", ease: "none" },
          0,
        );
        light(0);
      });

      // phones and reduced motion: each station lights when it scrolls into view
      mm.add("(max-width: 960px), (prefers-reduced-motion: reduce)", () => {
        items.forEach((el) =>
          ScrollTrigger.create({ trigger: el, start: "top 70%", onEnter: () => el.classList.add("lit") }),
        );
      });

      return () => mm.revert();
    },
    { scope: root },
  );

  return (
    <div ref={root} className="belt">
      <div className="belt-wrap">
        <div className="track" aria-hidden="true">
          <div className="treads" />
        </div>
        <span className="tag" aria-hidden="true">
          issue #1 · coupon crash
        </span>
        <ol className="stations">
          {stations.map((s, i) => (
            <li key={s.id} className="station">
              <div className="station-card">
                <div className="station-top">
                  <span className="station-no">Station {i + 1} of 5</span>
                  <span className="lamp" aria-hidden="true" />
                </div>
                <h3>{s.name}</h3>
                <span className="actor">Who acts: {s.actor}</span>
                <p>{s.body}</p>
              </div>
            </li>
          ))}
        </ol>
      </div>
    </div>
  );
}
