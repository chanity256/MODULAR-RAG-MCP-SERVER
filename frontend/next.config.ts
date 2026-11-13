import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Pure static export into `out/`, hosted by FastAPI (api.serve_ui) on the same port.
  output: "export",
  // Each route is emitted as `<route>/index.html`, so StaticFiles(html=True) can serve
  // `/overview/` without a per-route .html fallback. Deep links to unknown paths still
  // need the SPA fallback middleware (WEBUI_CHAT_SPEC section 5, item 6).
  trailingSlash: true,
  // next/image optimisation needs a server; disable it for the static export.
  images: { unoptimized: true },
  devIndicators: { position: "bottom-right" },
};

export default nextConfig;
