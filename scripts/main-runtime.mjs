import { execFileSync } from "node:child_process";
import { writeFileSync } from "node:fs";
import { join } from "node:path";

function git(repoRoot, ...args) {
    return execFileSync("git", ["-C", repoRoot, ...args], {
        encoding: "utf8",
        stdio: ["ignore", "pipe", "pipe"],
    }).trim();
}

export function assertMainRuntime(repoRoot) {
    const branch = git(repoRoot, "branch", "--show-current") || "detached HEAD";
    if (branch !== "main") {
        throw new Error(`WaveQuant's persistent development service must run from main (current branch: ${branch}).`);
    }
}

export function startMainRevisionPublisher(repoRoot, publicRoot, onBranchChange, intervalMs = 2_000) {
    assertMainRuntime(repoRoot);
    const revisionFile = join(publicRoot, "dev-runtime-revision.json");
    let lastRevision;
    let stopped = false;

    const publish = () => {
        if (stopped) return;
        try {
            assertMainRuntime(repoRoot);
            const revision = git(repoRoot, "rev-parse", "HEAD");
            if (revision === lastRevision) return;
            writeFileSync(revisionFile, `${JSON.stringify({ revision })}\n`, "utf8");
            lastRevision = revision;
            console.log(`[wavequant-web] Main revision: ${revision.slice(0, 12)}`);
        } catch (error) {
            if (error.message.includes("current branch:")) {
                stopped = true;
                clearInterval(timer);
                console.error(`[wavequant-web] ${error.message}`);
                onBranchChange?.();
            } else {
                console.error(`[wavequant-web] Unable to publish main revision: ${error.message}`);
            }
        }
    };

    const timer = setInterval(publish, intervalMs);
    publish();
    return () => {
        stopped = true;
        clearInterval(timer);
    };
}
