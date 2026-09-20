import { spawn, spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { readFileSync, readdirSync } from "node:fs";
import { relative, resolve } from "node:path";
import process from "node:process";

const LONG_RUNNING_FLAGS = new Set([
    "--api-only",
    "--watch-signals",
    "--watch-structures",
    "--watch-timeframes",
    "--port",
]);

export function shouldAutoRestartPython(args, environment = process.env) {
    if (environment.NODE_ENV && environment.NODE_ENV !== "development") return false;
    if (args[0] !== "-m" || args[1] !== "wavequant_api.cli") return false;
    return args.some((argument) => LONG_RUNNING_FLAGS.has(argument));
}

export function fingerprintPythonSources(sourceRoots) {
    const hash = createHash("sha256");
    for (const root of [...sourceRoots].map((path) => resolve(path)).sort()) {
        function visit(directory) {
            for (const entry of readdirSync(directory, { withFileTypes: true }).sort((left, right) =>
                left.name.localeCompare(right.name),
            )) {
                const path = resolve(directory, entry.name);
                if (entry.isDirectory()) visit(path);
                else if (entry.isFile() && entry.name.endsWith(".py")) {
                    hash.update(relative(root, path));
                    hash.update("\0");
                    hash.update(readFileSync(path));
                    hash.update("\0");
                }
            }
        }
        visit(root);
    }
    return hash.digest("hex");
}

export function createPythonSourceMonitor(
    sourceRoots,
    onChange,
    { intervalMs = 1_000, stablePolls = 2, onError = console.error } = {},
) {
    let published = fingerprintPythonSources(sourceRoots);
    let candidate = null;
    let candidatePolls = 0;
    let timer = null;

    function check() {
        const current = fingerprintPythonSources(sourceRoots);
        if (current === published) {
            candidate = null;
            candidatePolls = 0;
        } else {
            candidatePolls = current === candidate ? candidatePolls + 1 : 1;
            candidate = current;
            if (candidatePolls >= stablePolls) {
                published = current;
                candidate = null;
                candidatePolls = 0;
                onChange(current);
            }
        }
    }

    return {
        check,
        start() {
            if (timer) return;
            timer = setInterval(() => {
                try {
                    check();
                } catch (error) {
                    // An editor may temporarily replace a file between listing and reading it.
                    onError(error);
                }
            }, intervalMs);
        },
        stop() {
            if (timer) clearInterval(timer);
            timer = null;
        },
    };
}

function waitForClose(child, timeoutMs) {
    if (child.exitCode !== null || child.signalCode !== null) return Promise.resolve(true);
    return new Promise((resolveWait) => {
        const timer = setTimeout(() => {
            child.off("close", closed);
            resolveWait(false);
        }, timeoutMs);
        function closed() {
            clearTimeout(timer);
            resolveWait(true);
        }
        child.once("close", closed);
    });
}

async function terminateProcessTree(child) {
    if (!child.pid || child.exitCode !== null || child.signalCode !== null) return;
    if (process.platform === "win32") {
        // The venv launcher may spawn another python.exe; kill only this owned tree.
        const result = spawnSync("taskkill", ["/PID", String(child.pid), "/T", "/F"], {
            stdio: "ignore",
            windowsHide: true,
        });
        if (result.error) throw result.error;
    } else {
        try {
            process.kill(-child.pid, "SIGTERM");
        } catch (error) {
            if (error.code !== "ESRCH") throw error;
        }
    }
    if (await waitForClose(child, 5_000)) return;
    if (process.platform !== "win32") {
        try {
            process.kill(-child.pid, "SIGKILL");
        } catch (error) {
            if (error.code !== "ESRCH") throw error;
        }
    }
    if (!(await waitForClose(child, 2_000))) throw new Error(`Python process ${child.pid} did not exit`);
}

export function createPythonSupervisor({
    executable,
    args,
    cwd,
    sourceRoots,
    environment = process.env,
    pollIntervalMs = 1_000,
    restartDelayMs = 5_000,
    log = console.log,
}) {
    let child = null;
    let monitor = null;
    let retryTimer = null;
    let restartChain = Promise.resolve();
    let stopping = false;
    let restarting = false;
    let started = false;
    let stopPromise = null;

    function launch() {
        if (stopping) return;
        const next = spawn(executable, args, {
            cwd,
            env: environment,
            shell: false,
            stdio: "inherit",
            detached: process.platform !== "win32",
        });
        child = next;
        log(`[wavequant-api] Python service started (PID ${next.pid ?? "pending"}).`);
        next.on("error", (error) => log(`[wavequant-api] Python process error: ${error.message}`));
        next.once("close", (code, signal) => {
            if (child !== next) return;
            child = null;
            if (stopping || restarting) return;
            log(`[wavequant-api] Python service stopped (${signal || code}); retrying in ${restartDelayMs}ms.`);
            retryTimer = setTimeout(() => {
                retryTimer = null;
                launch();
            }, restartDelayMs);
        });
    }

    function restart() {
        if (stopping) return restartChain;
        if (retryTimer) clearTimeout(retryTimer);
        retryTimer = null;
        restartChain = restartChain
            .then(async () => {
                if (stopping) return;
                restarting = true;
                try {
                    if (child) await terminateProcessTree(child);
                    child = null;
                    launch();
                } finally {
                    restarting = false;
                }
            })
            .catch((error) => {
                log(`[wavequant-api] Python service restart failed: ${error.message}`);
                if (!stopping) {
                    retryTimer = setTimeout(() => {
                        retryTimer = null;
                        void restart();
                    }, restartDelayMs);
                }
            });
        return restartChain;
    }

    return {
        get pid() {
            return child?.pid ?? null;
        },
        start() {
            if (started) return;
            started = true;
            monitor = createPythonSourceMonitor(
                sourceRoots,
                () => {
                    log("[wavequant-api] Python source changed; restarting API/worker with the new engine.");
                    void restart();
                },
                { intervalMs: pollIntervalMs },
            );
            launch();
            monitor.start();
        },
        async stop() {
            if (stopPromise) return stopPromise;
            stopping = true;
            monitor?.stop();
            if (retryTimer) clearTimeout(retryTimer);
            retryTimer = null;
            stopPromise = (async () => {
                await restartChain;
                if (child) await terminateProcessTree(child);
                child = null;
            })();
            return stopPromise;
        },
    };
}
