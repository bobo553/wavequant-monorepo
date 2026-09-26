import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";

import { assertMainRuntime, startMainRevisionPublisher } from "./main-runtime.mjs";

function git(root, ...args) {
    return execFileSync("git", ["-C", root, ...args], { encoding: "utf8" }).trim();
}

test("only main publishes merged revisions for open development pages", async () => {
    const root = mkdtempSync(join(tmpdir(), "wavequant-main-runtime-"));
    const publicRoot = join(root, "public");
    mkdirSync(publicRoot);
    let stop;
    try {
        git(root, "init", "-b", "main");
        git(
            root,
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "--allow-empty",
            "-m",
            "initial",
        );
        assert.doesNotThrow(() => assertMainRuntime(root));
        stop = startMainRevisionPublisher(root, publicRoot, undefined, 20);
        const revisionFile = join(publicRoot, "dev-runtime-revision.json");
        const initial = JSON.parse(readFileSync(revisionFile, "utf8")).revision;
        assert.equal(initial, git(root, "rev-parse", "HEAD"));

        git(
            root,
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "--allow-empty",
            "-m",
            "merged feature",
        );
        const current = git(root, "rev-parse", "HEAD");
        await new Promise((resolve, reject) => {
            const deadline = setTimeout(() => reject(new Error("revision did not update")), 1_000);
            const poll = setInterval(() => {
                if (JSON.parse(readFileSync(revisionFile, "utf8")).revision !== current) return;
                clearTimeout(deadline);
                clearInterval(poll);
                resolve();
            }, 20);
        });

        stop();
        stop = undefined;
        git(root, "switch", "-c", "feat-test");
        assert.throws(() => assertMainRuntime(root), /must run from main/);
    } finally {
        stop?.();
        rmSync(root, { recursive: true, force: true });
    }
});
