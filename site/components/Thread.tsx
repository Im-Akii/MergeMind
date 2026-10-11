import { SPRITES } from "@/lib/scout";

// An issue thread as MergeMind runs it, using the sandbox kit's planted bug.
// Headings and phase lines follow src/mergemind/github/render.py and briefing.py.

function Bot({ time, children }: { time: string; children: React.ReactNode }) {
  return (
    <div className="entry">
      <div className="avatar bot" style={{ backgroundImage: `url(${SPRITES.directions})` }} aria-hidden="true" />
      <div className="comment">
        <div className="comment-head">
          <b>mergemind</b>
          <span className="badge">bot</span>
          <time>{time}</time>
        </div>
        <div className="comment-body">{children}</div>
      </div>
    </div>
  );
}

function Person({ who, initials, role, time, children }: { who: string; initials: string; role?: string; time: string; children: React.ReactNode }) {
  return (
    <div className="entry">
      <div className="avatar" aria-hidden="true">
        {initials}
      </div>
      <div className="comment">
        <div className="comment-head">
          <b>{who}</b>
          {role && <span className="badge">{role}</span>}
          <time>{time}</time>
        </div>
        <div className="comment-body">{children}</div>
      </div>
    </div>
  );
}

function Event({ merged, children }: { merged?: boolean; children: React.ReactNode }) {
  return (
    <div className="event">
      <span className={`dot${merged ? " merged" : ""}`} aria-hidden="true" />
      <span>{children}</span>
    </div>
  );
}

export default function Thread() {
  return (
    <>
      <div className="thread">
        <Person who="rhea-k" initials="RK" time="09:12">
          <h4>Applying a lowercase coupon code crashes the cart</h4>
          <p>
            When a customer types the coupon in lowercase, <code>apply_coupon</code> crashes instead of applying the
            discount.
          </p>
          <pre>{`cart.apply_coupon("save10")\nKeyError: 'save10'`}</pre>
        </Person>

        <Bot time="09:14">
          <h4>MergeMind · access offered</h4>
          <p>
            Open for the issue author to fix: reply <code>/contribute</code> to get access.
          </p>
          <p>
            <b>bug</b> · <code>apply_coupon</code> looks up the code exactly as typed, so lowercase codes raise
            KeyError.
          </p>
          <p>
            <b>Likely location:</b> <code>shopcart/cart.py</code> L19–20 <code>apply_coupon()</code>
          </p>
          <p>
            <b>Done when:</b>
          </p>
          <ul>
            <li>Coupon codes are case-insensitive: save10 gives 10% off.</li>
            <li>An unknown code raises a clear ValueError instead of KeyError.</li>
          </ul>
          <small>/contribute · /mergemind why · maintainers: /mergemind approve | accept | revoke | stop</small>
        </Bot>

        <Person who="rhea-k" initials="RK" time="09:20">
          <p className="slash">/contribute</p>
        </Person>

        <Bot time="09:21">
          <h4>MergeMind · starter briefing for rhea-k</h4>
          <p>
            You&apos;ve been invited as a collaborator with <b>push</b> access so you can fix this issue. Access ends
            in 14 days or when the fix is merged.
          </p>
          <p>
            <b>Where to start:</b> <code>shopcart/cart.py</code> L19–20 <code>apply_coupon</code>
          </p>
          <p>
            <b>Tests to run:</b> <code>tests/test_cart.py</code>
          </p>
          <p>
            <b>Regression test:</b> add a test that fails before your fix and passes after it.
          </p>
          <p>
            Work on <code>mergemind/issue-1</code> and say <code>Fixes #1</code> in the pull request.
          </p>
        </Bot>

        <Event>rhea-k opened pull request #2 · Make coupon codes case-insensitive</Event>

        <Bot time="10:02">
          <h4>MergeMind · ready for maintainer</h4>
          <p>
            Verification passed. A maintainer can merge with <code>/mergemind approve</code>.
          </p>
          <ul className="checks">
            <li>
              <span className="ok">PASS</span> No conflicts with main
            </li>
            <li>
              <span className="ok">PASS</span> Changes stay in scope: shopcart/cart.py, tests/test_cart.py
            </li>
            <li>
              <span className="ok">PASS</span> New test fails on the base branch and passes with the fix
            </li>
            <li>
              <span className="ok">PASS</span> Safety scan: no CI, dependency or secret changes
            </li>
            <li>
              <span className="ok">PASS</span> Acceptance criteria met: 2 of 2
            </li>
          </ul>
        </Bot>

        <Person who="you" initials="YO" role="maintainer" time="10:15">
          <p className="slash">/mergemind approve</p>
        </Person>

        <Event merged>mergemind merged commit 4f1c2ab into main · access for rhea-k removed</Event>

        <Bot time="10:47">
          <h4>MergeMind · done</h4>
          <p>Done. Merged and verified. Thank you!</p>
        </Bot>
      </div>
      <p className="caption">
        Example thread built from <code>mergemind sandbox-kit</code>. Run the kit to watch the real one.
      </p>
    </>
  );
}
