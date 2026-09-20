import type { NextConfig } from "next";
import { PHASE_DEVELOPMENT_SERVER } from "next/constants";

export default function configureNext(phase: string): NextConfig {
    const development = phase === PHASE_DEVELOPMENT_SERVER;
    const port = process.env.WAVEQUANT_API_PORT || "8765";
    return {
        allowedDevOrigins: development ? ["127.0.0.1"] : undefined,
        output: development ? undefined : "export",
        transpilePackages: ["@repo/design-system-web"],
        // A cold backtest may take longer than Next.js's 30s development rewrite default.
        // Keep the proxy alive slightly beyond the client's 300s backtest deadline.
        experimental: development ? { proxyTimeout: 305_000 } : undefined,
        ...(development
            ? {
                  async rewrites() {
                      return [{ source: "/api/:path*", destination: `http://127.0.0.1:${port}/api/:path*` }];
                  },
              }
            : {}),
    };
}
