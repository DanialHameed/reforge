/** @type {import('next').NextConfig} */
/**
 * Browser calls same-origin `/ingest-reforge/*`; Next proxies to FastAPI on IPv4 loopback,
 * avoiding Windows 11 localhost/IPv6/CORS issues vs `uvicorn` bound to 127.0.0.1:8000.
 * Override with BACKEND_INTERNAL_URL in `frontend/.env.local` if the API host differs.
 */
const backendInternalTunnel =
  (process.env.BACKEND_INTERNAL_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");

const nextConfig = {
  reactStrictMode: true,
  async rewrites() {
    return [
      {
        source: "/ingest-reforge/:path*",
        destination: `${backendInternalTunnel}/:path*`,
      },
    ];
  },
  images: {
    remotePatterns: [
      { protocol: "https", hostname: "res.cloudinary.com" },
      { protocol: "http", hostname: "res.cloudinary.com" },
      { protocol: "https", hostname: "lh3.googleusercontent.com" },
      { protocol: "https", hostname: "images.unsplash.com" },
      { protocol: "https", hostname: "pbs.twimg.com" },
      { protocol: "https", hostname: "scontent.xx.fbcdn.net" }
    ]
  },
  experimental: {
    serverActions: {
      allowedOrigins: [
        "localhost:3000",
        "127.0.0.1:3000",
        "localhost:3001",
        "127.0.0.1:3001",
      ],
    }
  }
};

export default nextConfig;

