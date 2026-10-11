import AutonomyDial from "@/components/AutonomyDial";
import Conveyor from "@/components/Conveyor";
import Dock from "@/components/Dock";
import { GuideProvider } from "@/components/Guide";
import Hero from "@/components/Hero";
import Rail from "@/components/Rail";
import Steps from "@/components/Steps";
import Thread from "@/components/Thread";
import { bench, commands, configKeys, envVars, faults, parts, REPO_URL, safety } from "@/lib/content";
import { BASE } from "@/lib/scout";

const NAV = [
  ["How it works", "#how"],
  ["On an issue", "#thread"],
  ["Setup", "#setup"],
  ["Policy file", "#config"],
  ["Commands", "#commands"],
  ["Fault codes", "#faults"],
];

export default function Page() {
  return (
    <>
      <header className="bar" id="top">
        <div className="wrap">
          <a className="wordmark" href="#top">
            Merge<span>Mind</span>
          </a>
          <nav aria-label="Sections">
            <ul>
              {NAV.map(([label, href]) => (
                <li key={href}>
                  <a href={href}>{label}</a>
                </li>
              ))}
              <li>
                <a href={REPO_URL} target="_blank" rel="noreferrer">
                  GitHub ↗
                </a>
              </li>
            </ul>
          </nav>
        </div>
      </header>

      <main>
        <Hero />

        <section className="band alt" id="how">
          <div className="wrap">
            <div className="section-head">
              <p className="eyebrow">How it works</p>
              <h2>Five stations, from issue to merge</h2>
              <p>
                An issue rides through in order. A person makes the two decisions that matter: the reporter chooses to
                fix it, and a maintainer chooses to merge.
              </p>
            </div>
            <Conveyor />
          </div>
        </section>

        <section className="band" id="thread">
          <div className="wrap">
            <div className="section-head">
              <p className="eyebrow">On an issue</p>
              <h2>What your repository sees</h2>
              <p>
                One card per issue, edited in place as things move. Every slash command below is typed by a person.
              </p>
            </div>
            <Thread />
          </div>
        </section>

        <GuideProvider>
        <div className="band alt">
          <div className="wrap manual">
            <Rail />
            <div className="doc">
              <section id="setup">
                <div className="section-head">
                  <p className="eyebrow">Setup</p>
                  <h2>Install it on your repository</h2>
                  <p>About twenty minutes. You need admin rights on the repository. Your progress is saved in this browser.</p>
                </div>

                <h3>Parts list</h3>
                <div className="table-wrap parts" style={{ marginTop: 16 }}>
                  <table>
                    <thead>
                      <tr>
                        <th scope="col">You need</th>
                        <th scope="col">What it&apos;s for</th>
                        <th scope="col"> </th>
                      </tr>
                    </thead>
                    <tbody>
                      {parts.map((p) => (
                        <tr key={p.item}>
                          <td>
                            <b>{p.item}</b>
                          </td>
                          <td>{p.why}</td>
                          <td>
                            <span className={`need ${p.need === "for testing" ? "testing" : p.need}`}>{p.need}</span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>

                <div className="modes">
                  <div className="mode pick">
                    <p className="eyebrow">
                      <b>Recommended</b>
                    </p>
                    <h4>Always on, in GitHub Actions</h4>
                    <p>
                      One engine pass per issue, comment, finished test run, and every 10 minutes. State is kept on a{" "}
                      <code>mergemind-state</code> branch. No server.
                    </p>
                  </div>
                  <div className="mode">
                    <p className="eyebrow">For trying it out</p>
                    <h4>Laptop mode</h4>
                    <p>
                      <code>mergemind run</code> polls every 15 seconds while your machine is on. Skip{" "}
                      <code>actions-kit</code> in step 4 and all of step 5; the rest is the same.
                    </p>
                  </div>
                </div>

                <Steps />
              </section>

              <section id="autonomy">
                <div className="section-head">
                  <p className="eyebrow">Autonomy</p>
                  <h2>How much it may do on its own</h2>
                  <p>Set it with <code>autonomy:</code> in the policy file. Turn the dial to see each level.</p>
                </div>
                <AutonomyDial />
              </section>

              <section id="config">
                <div className="section-head">
                  <p className="eyebrow">Reference</p>
                  <h2>The policy file</h2>
                  <p>
                    <code>.github/mergemind.yml</code> on the default branch. Durations take s, m, h or d, like{" "}
                    <code>14d</code> or <code>30m</code>.
                  </p>
                </div>
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th scope="col">Key</th>
                        <th scope="col">Default</th>
                        <th scope="col">What it does</th>
                      </tr>
                    </thead>
                    <tbody>
                      {configKeys.map((k) => (
                        <tr key={k.key}>
                          <td className="key">{k.key}</td>
                          <td className="def">{k.def}</td>
                          <td>{k.body}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>

              <section id="commands">
                <div className="section-head">
                  <p className="eyebrow">Reference</p>
                  <h2>Slash commands</h2>
                  <p>
                    Type them at the start of a line in an issue or pull request comment. Quoted lines and code blocks
                    are ignored. Maintainer means write permission on the repository; a contributor MergeMind invited
                    doesn&apos;t count.
                  </p>
                </div>
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th scope="col">Command</th>
                        <th scope="col">Who</th>
                        <th scope="col">What happens</th>
                      </tr>
                    </thead>
                    <tbody>
                      {commands.map((c) => (
                        <tr key={c.cmd}>
                          <td>
                            <kbd className="cmd">{c.cmd}</kbd>
                          </td>
                          <td className="who">{c.who}</td>
                          <td>{c.body}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>

              <section id="env">
                <div className="section-head">
                  <p className="eyebrow">Reference</p>
                  <h2>Environment variables</h2>
                  <p>
                    For laptop mode, put them in <code>.env.local</code> in the MergeMind folder. It is never committed,
                    and variables already set in your shell win.
                  </p>
                </div>
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th scope="col">Variable</th>
                        <th scope="col">Meaning</th>
                      </tr>
                    </thead>
                    <tbody>
                      {envVars.map((v) => (
                        <tr key={v.name}>
                          <td className="key">{v.name}</td>
                          <td>{v.body}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>

              <section id="faults">
                <div className="section-head">
                  <p className="eyebrow">Troubleshooting</p>
                  <h2>Fault codes</h2>
                  <p>What you see, why it happens, and the fix. Run preflight again after each fix.</p>
                </div>
                <div className="faults">
                  {faults.map((f) => (
                    <div key={f.code} className="fault">
                      <div>
                        <span className="lbl">You see</span>
                        <code>{f.code}</code>
                      </div>
                      <div>
                        <span className="lbl">Why</span>
                        <p>{f.cause}</p>
                      </div>
                      <div>
                        <span className="lbl">Fix</span>
                        <p>{f.fix}</p>
                      </div>
                    </div>
                  ))}
                </div>
              </section>

              <section id="safety">
                <div className="section-head">
                  <p className="eyebrow">Safety model</p>
                  <h2>What it will never do</h2>
                </div>
                <ul className="safety">
                  {safety.map((s) => (
                    <li key={s}>{s}</li>
                  ))}
                </ul>
              </section>

              <section id="bench">
                <div className="section-head">
                  <p className="eyebrow">Roadmap ideas</p>
                  <h2>On the bench</h2>
                  <p>
                    Features from CodeRabbit that would suit MergeMind. They are ideas for later. None of them exist in
                    MergeMind today.
                  </p>
                </div>
                <div className="tickets">
                  {bench.map((b) => (
                    <article key={b.name} className="ticket">
                      <span className="stampmark">Not built</span>
                      <h4>{b.name}</h4>
                      <p className="from">{b.from}</p>
                      <p>{b.here}</p>
                    </article>
                  ))}
                </div>
              </section>
            </div>
          </div>
        </div>
        <Dock />
        </GuideProvider>
      </main>

      <footer className="foot">
        <div className="wrap">
          <div>
            <p>
              Scout the robot comes from{" "}
              <a href="https://github.com/nilbuild/page-mascot" target="_blank" rel="noreferrer">
                page-mascot
              </a>{" "}
              by Kamran Ahmed, used under the{" "}
              <a href={`${BASE}/mascot/page-mascot-LICENSE.txt`}>MIT licence</a>.
            </p>
            <p>MergeMind runs on NVIDIA Nemotron models, Entire&apos;s code graph and neatlogs tracing.</p>
            <p>
              <a href={REPO_URL} target="_blank" rel="noreferrer">
                {REPO_URL.replace("https://", "")}
              </a>
            </p>
          </div>
          <a className="wordmark" href="#top">
            Merge<span>Mind</span>
          </a>
        </div>
      </footer>
    </>
  );
}
