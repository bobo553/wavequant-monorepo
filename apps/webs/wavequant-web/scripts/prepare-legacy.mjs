import { copyFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const workspaceRoot = join(dirname(fileURLToPath(import.meta.url)), "..");
const chartsRoot = join(workspaceRoot, "node_modules", "lightweight-charts");
const vendorRoot = join(workspaceRoot, "public", "vendor");

mkdirSync(vendorRoot, { recursive: true });
copyFileSync(
    join(chartsRoot, "dist", "lightweight-charts.standalone.production.js"),
    join(vendorRoot, "lightweight-charts.js"),
);
copyFileSync(join(chartsRoot, "LICENSE"), join(vendorRoot, "LICENSE"));
copyFileSync(join(workspaceRoot, "THIRD_PARTY_NOTICE.txt"), join(vendorRoot, "NOTICE"));

console.log(`Prepared legacy research assets: ${vendorRoot}`);
