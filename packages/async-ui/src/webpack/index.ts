import { gzipSync } from "node:zlib";

import type { AsyncViewResource } from "../types";

export type AsyncUIWebpackMode = "report" | "enforce";

/** 构建期检查配置；report 产生警告，enforce 将相同问题提升为构建错误。 */
export interface AsyncUIWebpackOptions {
    readonly mode: AsyncUIWebpackMode;
    readonly resources: readonly Readonly<AsyncViewResource>[];
    readonly manifestFilename?: string;
}

export interface AsyncUIManifestEntry extends AsyncViewResource {
    readonly assets: readonly string[];
    readonly gzipBytes: number;
    readonly initial: boolean;
}

/** 输出到构建产物的稳定清单格式，可供 CI、监控或发布平台消费。 */
export interface AsyncUIManifest {
    readonly version: 1;
    readonly resources: readonly AsyncUIManifestEntry[];
}

interface WebpackSource {
    source(): string | Uint8Array;
}

interface WebpackAsset {
    readonly source: WebpackSource;
}

interface WebpackModule {
    readonly resource?: string;
}

interface WebpackChunk {
    readonly name?: string;
    readonly files: Iterable<string>;
    canBeInitial?(): boolean;
}

interface WebpackCompilation {
    readonly chunks: Iterable<WebpackChunk>;
    readonly errors: Error[];
    readonly warnings: Error[];
    readonly hooks: {
        readonly processAssets: {
            tap(options: { name: string; stage: number }, callback: () => void): void;
        };
    };
    readonly chunkGraph?: {
        getChunkModulesIterable(chunk: WebpackChunk): Iterable<WebpackModule>;
    };
    getAsset(filename: string): WebpackAsset | undefined;
    emitAsset(filename: string, source: WebpackSource): void;
}

interface WebpackCompiler {
    readonly hooks: {
        readonly thisCompilation: {
            tap(name: string, callback: (compilation: WebpackCompilation) => void): void;
        };
    };
    readonly webpack: {
        readonly Compilation: { readonly PROCESS_ASSETS_STAGE_REPORT: number };
        readonly sources: {
            readonly RawSource: new (value: string) => WebpackSource;
        };
    };
}

interface WebpackConfig {
    readonly plugins?: readonly unknown[];
}

const PLUGIN_NAME = "AsyncUIWebpackPlugin";

// Webpack module.resource 使用系统路径，而业务 source 常用别名或 POSIX 路径，比较前需归一化。
function normalizePath(value: string): string {
    return value.replaceAll("\\", "/").replace(/^\.\//, "").toLowerCase();
}

function sourceMatches(moduleResource: string, declaredSource: string): boolean {
    const modulePath = normalizePath(moduleResource);
    const sourcePath = normalizePath(declaredSource).replace(/^@\//, "");
    return modulePath.endsWith(sourcePath) || modulePath.includes(`/${sourcePath}`);
}

function assetBytes(source: WebpackSource): Uint8Array {
    const value = source.source();
    return typeof value === "string" ? Buffer.from(value) : value;
}

/**
 * 校验异步 UI 是否真正形成独立 Chunk，并产出 gzip 体积清单。
 * 插件只存在于 `@repo/async-ui/webpack` 子入口，避免 Node zlib 被打入浏览器运行时代码。
 */
export class AsyncUIWebpackPlugin {
    public constructor(private readonly options: AsyncUIWebpackOptions) {
        if (options.mode !== "report" && options.mode !== "enforce") {
            throw new TypeError('Async UI webpack mode must be "report" or "enforce"');
        }
    }

    public apply(compiler: WebpackCompiler): void {
        compiler.hooks.thisCompilation.tap(PLUGIN_NAME, (compilation) => {
            compilation.hooks.processAssets.tap(
                {
                    name: PLUGIN_NAME,
                    stage: compiler.webpack.Compilation.PROCESS_ASSETS_STAGE_REPORT,
                },
                () => this.inspect(compilation, compiler.webpack.sources.RawSource),
            );
        });
    }

    private inspect(
        compilation: WebpackCompilation,
        RawSource: WebpackCompiler["webpack"]["sources"]["RawSource"],
    ): void {
        const issues: Error[] = [];
        const resources = this.validateResources(issues);
        const chunks = [...compilation.chunks];
        const initialChunks = chunks.filter((chunk) => chunk.canBeInitial?.() === true);
        const manifestEntries: AsyncUIManifestEntry[] = [];

        for (const resource of resources) {
            const chunk = chunks.find((candidate) => candidate.name === resource.chunkName);
            const assets = chunk
                ? [...chunk.files].filter((filename) => /\.(?:css|js|mjs)$/i.test(filename)).sort()
                : [];
            if (!chunk || assets.length === 0) {
                issues.push(
                    new Error(
                        `[async-ui/asset-missing] ${resource.id}: chunk "${resource.chunkName}" has no emitted JS/CSS asset`,
                    ),
                );
            }

            const initial = chunk?.canBeInitial?.() === true;
            if (initial) {
                issues.push(
                    new Error(
                        `[async-ui/initial-chunk] ${resource.id}: async chunk "${resource.chunkName}" is in the initial graph`,
                    ),
                );
            }

            if (compilation.chunkGraph) {
                // 即使命名 Chunk 存在，源模块静态进入首屏图也意味着懒加载边界已经失效。
                const penetrated = initialChunks.some((initialChunk) =>
                    [...compilation.chunkGraph!.getChunkModulesIterable(initialChunk)].some(
                        (module) =>
                            typeof module.resource === "string" && sourceMatches(module.resource, resource.source),
                    ),
                );
                if (penetrated) {
                    issues.push(
                        new Error(
                            `[async-ui/static-source] ${resource.id}: ${resource.source} is statically reachable from the initial graph`,
                        ),
                    );
                }
            }

            let gzipBytes = 0;
            for (const filename of assets) {
                const asset = compilation.getAsset(filename);
                if (!asset) {
                    issues.push(
                        new Error(
                            `[async-ui/asset-missing] ${resource.id}: emitted asset "${filename}" cannot be read`,
                        ),
                    );
                    continue;
                }
                // 预算以同一资源关联的 JS/CSS gzip 总和计算，更接近真实网络成本。
                gzipBytes += gzipSync(assetBytes(asset.source)).byteLength;
            }
            if (resource.budget?.gzipBytes !== undefined && gzipBytes > resource.budget.gzipBytes) {
                issues.push(
                    new Error(
                        `[async-ui/gzip-budget] ${resource.id}: ${gzipBytes} bytes exceeds ${resource.budget.gzipBytes} bytes`,
                    ),
                );
            }

            manifestEntries.push(
                Object.freeze({
                    ...resource,
                    assets: Object.freeze(assets),
                    gzipBytes,
                    initial,
                }),
            );
        }

        const manifest: AsyncUIManifest = Object.freeze({
            version: 1,
            resources: Object.freeze(manifestEntries.sort((left, right) => left.id.localeCompare(right.id))),
        });
        compilation.emitAsset(
            this.options.manifestFilename ?? "async-ui-manifest.json",
            new RawSource(`${JSON.stringify(manifest, null, 2)}\n`),
        );

        const target = this.options.mode === "enforce" ? compilation.errors : compilation.warnings;
        target.push(...issues);
    }

    /** 在分析 Chunk 前先保证每个声明都能唯一映射到一个业务资源。 */
    private validateResources(issues: Error[]): readonly Readonly<AsyncViewResource>[] {
        const ids = new Set<string>();
        const chunkNames = new Set<string>();
        const valid: Readonly<AsyncViewResource>[] = [];

        for (const resource of this.options.resources) {
            if (!resource.id.trim() || !resource.chunkName.trim() || !resource.source.trim()) {
                issues.push(new Error("[async-ui/invalid-resource] id, chunkName and source are required"));
                continue;
            }
            if (ids.has(resource.id)) {
                issues.push(new Error(`[async-ui/duplicate-id] ${resource.id}`));
                continue;
            }
            if (chunkNames.has(resource.chunkName)) {
                issues.push(new Error(`[async-ui/duplicate-chunk] ${resource.chunkName}`));
                continue;
            }
            ids.add(resource.id);
            chunkNames.add(resource.chunkName);
            valid.push(resource);
        }
        return valid;
    }
}

/** 在不修改调用方原对象的前提下，把 Async UI 插件追加到既有 Webpack 配置。 */
export function withAsyncUIWebpack<Config extends WebpackConfig>(
    config: Config,
    options: AsyncUIWebpackOptions,
): Config {
    return {
        ...config,
        plugins: [...(config.plugins ?? []), new AsyncUIWebpackPlugin(options)],
    };
}
