import type { NextConfig } from "next";

const defaultUpstream =
  process.env.NODE_ENV === 'development'
    ? 'http://localhost:8001'
    : 'http://api:8000'

const apiUpstream = (process.env.BOTUX_API_UPSTREAM ?? defaultUpstream).replace(/\/$/, '')

const nextConfig: NextConfig = {
  output: 'standalone',
  async rewrites() {
    return [
      {
        source: '/api/v2/:path*',
        destination: `${apiUpstream}/:path*`,
      },
    ]
  },
};

export default nextConfig;
