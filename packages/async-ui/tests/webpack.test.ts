import { describe, expect, it } from "vitest";

import {
    type AsyncUIManifest,
    type AsyncUIWebpackOptions,
    AsyncUIWebpackPlugin,
    withAsyncUIWebpack,
} from "../src/webpack";

type Compiler = Parameters<AsyncUIWebpackPlugin["apply"]>[0];

class RawSource {
    public constructor(private readonly value: string) {}

    public source(): string {
        return this.value;
    }
}

function runPlugin(options: AsyncUIWebpackOptions, initial = false) {
    let onCompilation!: (compilation: unknown) => void;
    let processAssets!: () => void;
    const emitted = new Map<string, RawSource>();
    const chunk = {
        name: "reward-popup",
        files: new Set(["reward-popup.js"]),
        canBeInitial: () => initial,
    };
    const compilation = {
        chunks: [chunk],
        errors: [] as Error[],
        warnings: [] as Error[],
        hooks: {
            processAssets: {
                tap: (_tapOptions: unknown, callback: () => void) => {
                    processAssets = callback;
                },
            },
        },
        chunkGraph: {
            getChunkModulesIterable: () => (initial ? [{ resource: "C:/project/src/popups/reward-popup.tsx" }] : []),
        },
        getAsset: (filename: string) =>
            filename === "reward-popup.js" ? { source: new RawSource("export const reward = 'loaded';") } : undefined,
        emitAsset: (filename: string, source: RawSource) => emitted.set(filename, source),
    };
    const compiler = {
        hooks: {
            thisCompilation: {
                tap: (_name: string, callback: (value: unknown) => void) => {
                    onCompilation = callback;
                },
            },
        },
        webpack: {
            Compilation: { PROCESS_ASSETS_STAGE_REPORT: 5_000 },
            sources: { RawSource },
        },
    };

    new AsyncUIWebpackPlugin(options).apply(compiler as unknown as Compiler);
    onCompilation(compilation);
    processAssets();
    return { compilation, emitted };
}

const resource = {
    id: "reward",
    chunkName: "reward-popup",
    source: "src/popups/reward-popup.tsx",
    preload: "rare",
} as const;

describe("AsyncUIWebpackPlugin", () => {
    it("输出确定性的资源清单", () => {
        const { compilation, emitted } = runPlugin({ mode: "enforce", resources: [resource] });
        const manifest = JSON.parse(emitted.get("async-ui-manifest.json")!.source()) as AsyncUIManifest;

        expect(compilation.errors).toEqual([]);
        expect(manifest).toMatchObject({
            version: 1,
            resources: [
                {
                    id: "reward",
                    assets: ["reward-popup.js"],
                    initial: false,
                },
            ],
        });
        expect(manifest.resources[0]?.gzipBytes).toBeGreaterThan(0);
    });

    it("enforce 模式阻断初始图、静态穿透和 gzip 超限", () => {
        const { compilation } = runPlugin(
            {
                mode: "enforce",
                resources: [{ ...resource, budget: { gzipBytes: 1 } }],
            },
            true,
        );
        const messages = compilation.errors.map(({ message }) => message);

        expect(messages.some((message) => message.includes("async-ui/initial-chunk"))).toBe(true);
        expect(messages.some((message) => message.includes("async-ui/static-source"))).toBe(true);
        expect(messages.some((message) => message.includes("async-ui/gzip-budget"))).toBe(true);
    });

    it("report 模式只产生 warning，并保留原 webpack 配置", () => {
        const originalPlugin = { name: "existing" };
        const config = withAsyncUIWebpack(
            { mode: "production", plugins: [originalPlugin] },
            { mode: "report", resources: [resource] },
        );
        const plugin = config.plugins[1];

        expect(config.mode).toBe("production");
        expect(config.plugins[0]).toBe(originalPlugin);
        expect(plugin).toBeInstanceOf(AsyncUIWebpackPlugin);

        const { compilation } = runPlugin({
            mode: "report",
            resources: [{ ...resource, chunkName: "missing" }],
        });
        expect(compilation.errors).toEqual([]);
        expect(compilation.warnings[0]?.message).toContain("async-ui/asset-missing");
    });
});
