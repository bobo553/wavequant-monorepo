import { randomUUID } from "node:crypto";
import { watch, writeFileSync } from "node:fs";
import { join } from "node:path";

/** Public module scripts need a page reload; Next's component HMR does not reload them. */
export function startSourceRevisionPublisher(publicRoot) {
    let timer = null;
    const publish = () => {
        writeFileSync(join(publicRoot, "dev-runtime-revision.json"), `${JSON.stringify({ revision: randomUUID() })}\n`);
    };
    publish();
    const watcher = watch(publicRoot, { recursive: true }, (_event, filename) => {
        if (!filename || !/\.(?:js|mjs|css)$/.test(String(filename))) return;
        clearTimeout(timer);
        timer = setTimeout(publish, 1_000);
    });
    watcher.on("error", (error) => console.error(`[wavequant-web] Source reload watcher: ${error.message}`));
    return () => {
        clearTimeout(timer);
        watcher.close();
    };
}
