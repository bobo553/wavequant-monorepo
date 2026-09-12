import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const workspaceRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const monorepoRoot = resolve(workspaceRoot, "../../..");
const pnpmCli = process.env.npm_execpath;
const apiPort = process.env.WAVEQUANT_API_PORT || "8765";
const firstExisting = (...candidates) => candidates.find((candidate) => candidate && existsSync(candidate));
const resultsRoot = firstExisting(
    process.env.WAVEQUANT_RESULTS_ROOT,
    resolve(monorepoRoot, "packages/wavequant-core/results/operations_v1"),
    process.platform === "win32" ? "E:\\WorkSpace\\股票\\results\\operations_v1" : undefined,
);
const tdxRoot = firstExisting(process.env.WAVEQUANT_TDX_ROOT, process.platform === "win32" ? "D:\\TDX" : undefined);

const children = [];
const run = (command, args, cwd) => {
    const child = spawn(command, args, { cwd, env: process.env, shell: false, stdio: "inherit" });
    children.push(child);
    return child;
};
const runPnpm = (args, cwd) =>
    pnpmCli
        ? run(process.execPath, [pnpmCli, ...args], cwd)
        : run(process.platform === "win32" ? "pnpm.cmd" : "pnpm", args, cwd);

if (resultsRoot) {
    const apiArguments = [
        "--filter",
        "wavequant-api",
        "python",
        "-m",
        "wavequant_api.cli",
        "--root",
        resultsRoot,
        "--port",
        apiPort,
        "--api-only",
        "--allow-origin",
        "http://localhost:3003",
        "--allow-origin",
        "http://127.0.0.1:3003",
    ];
    if (tdxRoot) apiArguments.push("--tdx-root", tdxRoot);
    console.log(`[wavequant-web] API data: ${resultsRoot}`);
    console.log(`[wavequant-web] TDX data: ${tdxRoot || "not configured"}`);
    runPnpm(apiArguments, monorepoRoot);
} else {
    console.warn(
        "[wavequant-web] No sealed result directory found. Set WAVEQUANT_RESULTS_ROOT to enable the research API.",
    );
}

const next = runPnpm(["exec", "next", "dev", "--turbopack", "--port", "3003"], workspaceRoot);
let stopping = false;
const stop = (code = 0) => {
    if (stopping) return;
    stopping = true;
    for (const child of children) {
        if (!child.killed) child.kill("SIGINT");
    }
    process.exitCode = code;
};

process.on("SIGINT", () => stop(0));
process.on("SIGTERM", () => stop(0));
for (const child of children) {
    child.on("error", (error) => {
        console.error(`[wavequant-web] Failed to start child process: ${error.message}`);
        stop(1);
    });
    child.on("exit", (code) => {
        if (!stopping && child === next) stop(code ?? 1);
        if (!stopping && child !== next && code !== 0) stop(code ?? 1);
    });
}
