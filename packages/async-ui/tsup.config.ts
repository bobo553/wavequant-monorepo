import { baseConfig } from "@repo/tsup";
import { defineConfig } from "tsup";

export default defineConfig({
    ...baseConfig,
    entry: ["src/index.ts", "src/webpack/index.ts"],
    external: ["react"],
});
