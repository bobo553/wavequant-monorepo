import { baseConfig } from "@repo/tsup";
import { type Options, defineConfig } from "tsup";

const config: Options = {
    ...baseConfig,
    banner: {
        js: "#!/usr/bin/env node",
    },
    dts: false,
    entry: ["src/cli.ts"],
    format: ["esm"],
    splitting: false,
};

export default defineConfig(config);
