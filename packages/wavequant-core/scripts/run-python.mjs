import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const workspaceRoot = join(dirname(fileURLToPath(import.meta.url)), "..");
const configuredPython = process.env.WAVEQUANT_PYTHON;
const localPython =
    process.platform === "win32"
        ? join(workspaceRoot, ".venv", "Scripts", "python.exe")
        : join(workspaceRoot, ".venv", "bin", "python");
const executable = configuredPython || (existsSync(localPython) ? localPython : "python");
const result = spawnSync(executable, process.argv.slice(2), {
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
