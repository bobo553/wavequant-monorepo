import { defineConfig } from "@playwright/test";
import { baseConfig } from "@repo/playwright";

export default defineConfig({
    ...baseConfig,
    testDir: "./tests/e2e",
    outputDir: "./test-results",
    use: {
        ...baseConfig.use,
        baseURL: "http://localhost:3003",
        screenshot: "only-on-failure",
    },
    webServer: {
        command: "pnpm dev",
        url: "http://localhost:3003",
        reuseExistingServer: !process.env.CI,
    },
});
