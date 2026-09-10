import { defineConfig } from "vitest/config";

export default defineConfig({
    test: {
        coverage: {
            include: ["src/**/*.ts"],
            provider: "v8",
            reportsDirectory: "coverage",
        },
        environment: "node",
        globals: true,
        include: ["tests/**/*.test.ts"],
    },
});
