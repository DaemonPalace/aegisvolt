import type { NextConfig } from "next";

// Static export (SPEC §12): `next build` writes plain files to out/, served by the env's S3 website.
// trailingSlash makes /saga/ → saga/index.html, which the S3 index document resolves.
const nextConfig: NextConfig = { output: "export", trailingSlash: true, images: { unoptimized: true } };

export default nextConfig;
