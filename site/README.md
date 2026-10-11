# MergeMind field manual

The setup guide and documentation site for MergeMind: Next.js (static export), GSAP, no UI framework.

```bash
npm install
npm run dev      # http://localhost:3000
npm run build    # static site in out/
```

All the copy lives in `lib/content.ts`. Keep it in step with `src/mergemind/` when config keys, commands or
token permissions change.

Deploys to GitHub Pages through `.github/workflows/site.yml` on every push to `main` that touches `site/`.
Turn it on once in Settings → Pages → Source: GitHub Actions. The build sets `NEXT_PUBLIC_BASE_PATH` to
`/<repo-name>`; leave it empty for a custom domain or a user site.

Scout the robot is from [page-mascot](https://github.com/nilbuild/page-mascot) by Kamran Ahmed (MIT, see
`public/mascot/page-mascot-LICENSE.txt`).
