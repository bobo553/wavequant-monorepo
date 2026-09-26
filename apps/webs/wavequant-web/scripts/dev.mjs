import { spawn, spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

import { assertMainRuntime, startMainRevisionPublisher } from "../../../../scripts/main-runtime.mjs";
import {
    isInfrastructureConfigured,
    loadEnvironmentDefaults,
    resolveStructureWorkerCount,
    shouldStartBundledInfrastructure,
} from "./dev-infrastructure.mjs";

const workspaceRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const monorepoRoot = resolve(workspaceRoot, "../../..");
assertMainRuntime(monorepoRoot);
const apiWorkspaceRoot = resolve(monorepoRoot, "apps/servers/wavequant-api");
const infrastructureEnvironment = resolve(apiWorkspaceRoot, ".env.infrastructure");
const composeFile = resolve(apiWorkspaceRoot, "compose.yaml");

if (existsSync(infrastructureEnvironment)) {
    loadEnvironmentDefaults(infrastructureEnvironment);
    console.log("[wavequant-web] Loaded local SQL/Redis configuration.");
}

const pnpmCli = process.env.npm_execpath;
const apiPort = process.env.WAVEQUANT_API_PORT || "8765";
const webPort = process.env.WAVEQUANT_WEB_PORT || "3003";
const webOrigin = `http://127.0.0.1:${webPort}`;
const firstExisting = (...candidates) => candidates.find((candidate) => candidate && existsSync(candidate));
const resultsRoot = firstExisting(
    process.env.WAVEQUANT_RESULTS_ROOT,
    resolve(monorepoRoot, "packages/wavequant-core/results/operations_v1"),
    process.platform === "win32" ? "E:\\WorkSpace\\股票\\results\\operations_v1" : undefined,
);
const tdxRoot = firstExisting(process.env.WAVEQUANT_TDX_ROOT, process.platform === "win32" ? "D:\\TDX" : undefined);

const children = new Set();
const restartTimers = new Set();
let stopping = false;
let stopRevisionPublisher;

function stop(code = 0) {
    if (stopping) return;
    stopping = true;
    stopRevisionPublisher?.();
    for (const timer of restartTimers) clearTimeout(timer);
    for (const child of children) {
        if (!child.killed) child.kill("SIGINT");
    }
    process.exitCode = code;
}

const run = (command, args, cwd, { label, restart = false } = {}) => {
    const child = spawn(command, args, { cwd, env: process.env, shell: false, stdio: "inherit" });
    children.add(child);
    let settled = false;
    const handleExit = (code, error) => {
        if (settled) return;
        settled = true;
        children.delete(child);
        if (stopping) return;
        if (!restart) {
            if (error) console.error(`[wavequant-web] ${label} failed: ${error.message}`);
            stop(code ?? 1);
            return;
        }
        console.error(
            `[wavequant-web] ${label} stopped${error ? `: ${error.message}` : ` with code ${code}`}; restarting in 5s.`,
        );
        const timer = setTimeout(() => {
            restartTimers.delete(timer);
            run(command, args, cwd, { label, restart: true });
        }, 5_000);
        restartTimers.add(timer);
    };
    child.on("error", (error) => handleExit(1, error));
    child.on("exit", (code) => handleExit(code));
    return child;
};
const runPnpm = (args, cwd, options) =>
    pnpmCli
        ? run(process.execPath, [pnpmCli, ...args], cwd, options)
        : run(process.platform === "win32" ? "pnpm.cmd" : "pnpm", args, cwd, options);
const runCommand = (command, args, cwd) => {
    const result = spawnSync(command, args, { cwd, env: process.env, shell: false, stdio: "inherit" });
    if (result.error) throw result.error;
    if (result.status !== 0) throw new Error(`${command} exited with code ${result.status}`);
};
const runPnpmCommand = (args, cwd) =>
    pnpmCli
        ? runCommand(process.execPath, [pnpmCli, ...args], cwd)
        : runCommand(process.platform === "win32" ? "pnpm.cmd" : "pnpm", args, cwd);

if (shouldStartBundledInfrastructure()) {
    console.log("[wavequant-web] Starting bundled MySQL/Redis and waiting for health checks.");
    runCommand(
        "docker",
        [
            "compose",
            "--env-file",
            infrastructureEnvironment,
            "-f",
            composeFile,
            "up",
            "-d",
            "--wait",
            "--wait-timeout",
            "90",
        ],
        apiWorkspaceRoot,
    );
}

const infrastructureConfigured = isInfrastructureConfigured();
if (infrastructureConfigured) {
    runPnpmCommand(["--filter", "wavequant-api", "python", "-m", "wavequant_api.cli", "--init-database"], monorepoRoot);
} else {
    console.warn(
        `[wavequant-web] Structure snapshots are disabled. Create ${infrastructureEnvironment} or configure WAVEQUANT_DATABASE_URL.`,
    );
}

const apiCliArguments = (...extra) => [
    "--filter",
    "wavequant-api",
    "python",
    "-m",
    "wavequant_api.cli",
    "--root",
    resultsRoot,
    ...(tdxRoot ? ["--tdx-root", tdxRoot] : []),
    ...extra,
];

if (resultsRoot) {
    const apiArguments = apiCliArguments(
        "--port",
        apiPort,
        "--api-only",
        "--web-url",
        webOrigin,
        "--allow-origin",
        webOrigin,
        "--allow-origin",
        `http://localhost:${webPort}`,
    );
    console.log(`[wavequant-web] API data: ${resultsRoot}`);
    console.log(`[wavequant-web] TDX data: ${tdxRoot || "not configured"}`);
    runPnpm(apiArguments, monorepoRoot, { label: "API" });

    if (infrastructureConfigured) {
        runPnpm(apiCliArguments("--watch-signals", "--signal-source", "tdx"), monorepoRoot, {
            label: "TDX signal worker",
            restart: true,
        });
        const workerCount = resolveStructureWorkerCount();
        for (let index = 0; index < workerCount; index++) {
            runPnpm(
                apiCliArguments(
                    "--watch-signals",
                    "--signal-family",
                    "structure",
                    "--signal-source",
                    "akshare",
                    "--signal-shard-count",
                    String(workerCount),
                    "--signal-shard-index",
                    String(index),
                ),
                monorepoRoot,
                { label: `AkShare structure worker ${index + 1}/${workerCount}`, restart: true },
            );
        }
        console.log(`[wavequant-web] Structure workers: TDX + ${workerCount} AkShare shard(s).`);
    }
} else {
    console.warn(
        "[wavequant-web] No sealed result directory found. Set WAVEQUANT_RESULTS_ROOT to enable the research API.",
    );
}

runPnpm(["exec", "next", "dev", "--turbopack", "--port", webPort], workspaceRoot, { label: "Next.js" });
stopRevisionPublisher = startMainRevisionPublisher(monorepoRoot, resolve(workspaceRoot, "public"), () => stop(1));

process.on("SIGINT", () => stop(0));
process.on("SIGTERM", () => stop(0));
