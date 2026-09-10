import { defineConfig } from "@playwright/test";
import { baseConfig } from "@repo/playwright";

export default defineConfig({
    ...baseConfig,
    outputDir: "./test-results",
    use: {
        ...baseConfig.use,
        baseURL: "http://localhost:3002",
        screenshot: "only-on-failure",
    },
    webServer: {
        command: "pnpm dev",
        url: "http://localhost:3002",
        reuseExistingServer: !process.env.CI,
    },
});
