import { cp, mkdir } from "node:fs/promises";
import { join } from "node:path";
import { prepareRuntimeAssets } from "./prepare-runtime-assets.mjs";
import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const nextCli = require.resolve("next/dist/bin/next");
const source = fileURLToPath(new URL("..", import.meta.url));
const cwd = join(source, ".codex-runtime", `production-${process.pid}`);
// 生产资源写入专属目录，开发源码和原生模块保持可读。
await mkdir(cwd, { recursive: true });
for (const name of ["src", "public", "package.json", "next.config.ts", "tsconfig.json", "postcss.config.mjs", "next-env.d.ts"]) {
    await cp(join(source, name), join(cwd, name), { recursive: true, force: true });
}
await prepareRuntimeAssets(join(cwd, "public"));
const env = { ...process.env, WAVEQUANT_API_PORT: process.env.WAVEQUANT_API_PORT || "8010" };
const port = process.env.WAVEQUANT_WEB_PORT || "3003";
let active;
let stopping = false;
for (const signal of ["SIGINT", "SIGTERM"]) process.on(signal, () => { stopping = true; active?.kill(signal); });
function run(args) {
    return new Promise((resolve, reject) => {
        active = spawn(process.execPath, [nextCli, ...args], { cwd, env, stdio: "inherit" });
        active.once("error", reject);
        active.once("exit", (code) => code === 0 || stopping ? resolve() : reject(new Error(`Next.js exited with code ${code}`)));
    });
}
// 日常查看使用压缩生产包；修改代码后重新执行，开发调试继续使用dev:web。
await run(["build", "--webpack"]);
if (!stopping) await run(["start", "--hostname", "127.0.0.1", "--port", port]);
