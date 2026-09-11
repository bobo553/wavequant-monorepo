import { copyFileSync, mkdirSync, readdirSync, rmSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const workspaceRoot = join(dirname(fileURLToPath(import.meta.url)), "..");
const sourceRoot = join(workspaceRoot, "src");
const outputRoot = join(workspaceRoot, "dist");
const vendorRoot = join(outputRoot, "vendor");

rmSync(outputRoot, { force: true, recursive: true });
mkdirSync(vendorRoot, { recursive: true });

for (const entry of readdirSync(sourceRoot, { withFileTypes: true })) {
    if (entry.isFile()) copyFileSync(join(sourceRoot, entry.name), join(outputRoot, entry.name));
}

const chartsRoot = join(workspaceRoot, "node_modules", "lightweight-charts");
copyFileSync(
    join(chartsRoot, "dist", "lightweight-charts.standalone.production.js"),
    join(vendorRoot, "lightweight-charts.js"),
);
copyFileSync(join(chartsRoot, "LICENSE"), join(vendorRoot, "LICENSE"));
copyFileSync(join(workspaceRoot, "THIRD_PARTY_NOTICE.txt"), join(vendorRoot, "NOTICE"));

console.log(`WaveQuant Web build created: ${outputRoot}`);
