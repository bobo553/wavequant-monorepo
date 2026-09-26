import { spawn } from "node:child_process";
import { dirname, resolve } from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

import { assertMainRuntime, startMainRevisionPublisher } from "../../../../scripts/main-runtime.mjs";

const workspaceRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const monorepoRoot = resolve(workspaceRoot, "../../..");
assertMainRuntime(monorepoRoot);

const pnpmCli = process.env.npm_execpath;
const command = pnpmCli ? process.execPath : process.platform === "win32" ? "pnpm.cmd" : "pnpm";
const args = [...(pnpmCli ? [pnpmCli] : []), "exec", "next", "dev", "--turbopack", "--port", "3003"];
const next = spawn(command, args, { cwd: workspaceRoot, env: process.env, stdio: "inherit" });
const stopPublisher = startMainRevisionPublisher(monorepoRoot, resolve(workspaceRoot, "public"), () =>
    next.kill("SIGINT"),
);

next.on("error", (error) => {
    console.error(`[wavequant-web] Unable to start Next.js: ${error.message}`);
    stopPublisher();
    process.exitCode = 1;
});
next.on("exit", (code) => {
    stopPublisher();
    process.exitCode = code ?? 1;
});
process.on("SIGINT", () => next.kill("SIGINT"));
process.on("SIGTERM", () => next.kill("SIGTERM"));
