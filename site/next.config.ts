import type { NextConfig } from "next";

// Static export: `npm run build` writes plain HTML/CSS/JS to out/, ready for GitHub Pages.
// On a project page (user.github.io/MergeMind) set NEXT_PUBLIC_BASE_PATH=/MergeMind at build time.
const basePath = process.env.NEXT_PUBLIC_BASE_PATH || "";

const config: NextConfig = {
  output: "export",
  basePath,
  trailingSlash: true,
  images: { unoptimized: true },
};

export default config;
