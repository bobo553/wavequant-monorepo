import { defineConfig } from "@playwright/test";
import { baseConfig } from "@repo/playwright";

export default defineConfig({
    ...baseConfig,
    testDir: "./tests/e2e",
    outputDir: "./test-results",
    workers: process.env.WAVEQUANT_E2E_WEB_PORT ? 2 : baseConfig.workers,
    use: {
        ...baseConfig.use,
        baseURL: `http://localhost:${process.env.WAVEQUANT_E2E_WEB_PORT || "3003"}`,
        screenshot: "only-on-failure",
    },
    webServer: {
        command: "pnpm dev",
        url: process.env.WAVEQUANT_E2E_WEB_PORT
            ? `http://localhost:${process.env.WAVEQUANT_E2E_WEB_PORT}/api/catalog`
            : "http://localhost:3003",
        reuseExistingServer: !process.env.CI && !process.env.WAVEQUANT_E2E_WEB_PORT,
        timeout: 180_000,
    },
});
