import path from "node:path";

import swc from "unplugin-swc";
import { defineConfig } from "vitest/config";

export const createNestE2eConfig = (rootDir: string) =>
    defineConfig({
        oxc: false,
        plugins: [swc.vite({ module: { type: "es6" } })],
        resolve: {
            alias: { "@": path.join(rootDir, "src") },
            tsconfigPaths: true,
        },
        test: {
            globals: true,
            environment: "node",
            setupFiles: ["./vitest.setup.ts"],
            include: ["**/*.spec.ts"],
            hookTimeout: 30000,
        },
    });
