/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: false, // OpenSeadragon manages DOM nodes directly
  output: "standalone",
  async rewrites() {
    const apiBaseUrl = (process.env.NEXT_PUBLIC_API_URL || "").replace(/\/+$/, "");
    if (!apiBaseUrl) return [];
    return [{
        source: "/api/v1/:path*",
        destination: `${apiBaseUrl}/api/v1/:path*`,
    }];
  },
};

module.exports = nextConfig;
