import { spawnSync } from "node:child_process";
import { readdirSync } from "node:fs";
import { dirname, extname, join } from "node:path";
import { fileURLToPath } from "node:url";

const workspaceRoot = join(dirname(fileURLToPath(import.meta.url)), "..");
const sourceRoots = [join(workspaceRoot, "src"), join(workspaceRoot, "tests")];
const supportedExtensions = new Set([".cjs", ".js", ".mjs"]);

function collectJavaScript(directory) {
    return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
        const entryPath = join(directory, entry.name);
        if (entry.isDirectory()) return entry.name === "node_modules" ? [] : collectJavaScript(entryPath);
        return entry.isFile() && supportedExtensions.has(extname(entry.name)) ? [entryPath] : [];
    });
}

for (const filePath of sourceRoots.flatMap(collectJavaScript)) {
    const result = spawnSync(process.execPath, ["--check", filePath], { stdio: "inherit" });
    if (result.status !== 0) process.exit(result.status ?? 1);
}

console.log("Web JavaScript syntax validation passed.");
