/** @type {import('next').NextConfig} */
const nextConfig = {
  // Keep `next dev`'s generated assets separate from production `.next` output.
  distDir: process.env.NODE_ENV === "development" ? ".next-dev" : ".next",
};
export default nextConfig;
