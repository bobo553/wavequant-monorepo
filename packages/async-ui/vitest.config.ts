import { baseConfig } from "@repo/vitest/base";
import { defineConfig } from "vitest/config";

export default defineConfig({
    test: {
        ...baseConfig,
        environment: "jsdom",
    },
});
