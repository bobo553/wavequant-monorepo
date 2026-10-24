import { readdir, readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const { build } = require("esbuild");
const { minify } = require("next/dist/compiled/terser");

/** 仅输出到暂存目录；共享侧栏实例，动态功能保留独立下载边界。 */
export async function prepareRuntimeAssets(directory) {
    const target = resolve(directory);
    const source = resolve(fileURLToPath(new URL("../public", import.meta.url)));
    if (target === source || !target.includes(".codex-runtime")) throw new Error("Asset minification requires a separate runtime staging directory");
    let before = 0, after = 0;
    for (const entry of await readdir(target, { withFileTypes: true })) {
        if (!entry.isFile() || !entry.name.endsWith(".js")) continue;
        const path = resolve(target, entry.name);
        const input = await readFile(path, "utf8");
        const result = await minify(input, {
            module: entry.name !== "watchlist-first-paint.js",
            compress: true, mangle: true, format: { comments: false },
        });
        if (!result.code) throw new Error(`Empty runtime asset: ${entry.name}`);
        await writeFile(path, result.code, "utf8");
        before += Buffer.byteLength(input); after += Buffer.byteLength(result.code);
    }
    const result = await build({
        entryPoints: {
            app: resolve(source, "app.js"),
            "watchlist-bootstrap": resolve(source, "watchlist-bootstrap.js"),
        },
        outdir: target,
        entryNames: "[name]",
        chunkNames: "runtime/[name]-[hash]",
        bundle: true,
        splitting: true,
        format: "esm",
        platform: "browser",
        target: "es2022",
        minify: true,
        metafile: true,
        legalComments: "none",
    });
    console.log(JSON.stringify({ assetPreparation: { before, after,
        bundledChunks: Object.keys(result.metafile.outputs).length } }));
}
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) await prepareRuntimeAssets(process.argv[2]);
