import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { createPythonSupervisor, shouldAutoRestartPython } from "./python-dev-restart.mjs";

const workspaceRoot = join(dirname(fileURLToPath(import.meta.url)), "..");
const configuredPython = process.env.WAVEQUANT_API_PYTHON;
const localPython =
    process.platform === "win32"
        ? join(workspaceRoot, ".venv", "Scripts", "python.exe")
        : join(workspaceRoot, ".venv", "bin", "python");
const executable = configuredPython || (existsSync(localPython) ? localPython : "python");
const args = process.argv.slice(2);

if (shouldAutoRestartPython(args)) {
    const monorepoRoot = resolve(workspaceRoot, "../../..");
    const supervisor = createPythonSupervisor({
        executable,
        args,
        cwd: workspaceRoot,
        sourceRoots: [
            join(monorepoRoot, "packages", "wavequant-core", "src", "wavequant"),
            join(workspaceRoot, "src", "wavequant_api"),
        ],
    });
    supervisor.start();
    let shuttingDown = false;
    const shutdown = () => {
        if (shuttingDown) return;
        shuttingDown = true;
        void supervisor.stop().then(
            () => {
                process.exitCode = 0;
            },
            (error) => {
                console.error(`[wavequant-api] Unable to stop Python process: ${error.message}`);
                process.exitCode = 1;
            },
        );
    };
    process.on("SIGINT", shutdown);
    process.on("SIGTERM", shutdown);
} else {
    const result = spawnSync(executable, args, {
        cwd: workspaceRoot,
        env: process.env,
        shell: false,
        stdio: "inherit",
    });

    if (result.error) {
        console.error(`Unable to start Python (${executable}): ${result.error.message}`);
        process.exit(1);
    }

    process.exit(result.status ?? 1);
}
