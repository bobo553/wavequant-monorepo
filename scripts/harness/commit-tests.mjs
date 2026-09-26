import { execFileSync, spawnSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { createServer } from "node:net";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const repositoryRoot = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const git = (...args) => execFileSync("git", args, { cwd: repositoryRoot, encoding: "utf8" });
const paths = (output) => output.split("\0").filter(Boolean);
const staged = paths(git("diff", "--cached", "--name-only", "-z", "--diff-filter=ACMR"));
const manifests = paths(git("ls-files", "-z", "--", "apps", "packages"))
    .filter((path) => path.endsWith("/package.json"))
    .map((path) => ({
        path,
        directory: dirname(path).replaceAll("\\", "/"),
        data: JSON.parse(readFileSync(join(repositoryRoot, path), "utf8")),
    }))
    .filter(({ data }) => data.name);

const names = new Set(manifests.map(({ data }) => data.name));
const selected = new Set();
for (const path of staged) {
    const owner = manifests
        .filter(({ directory }) => path.startsWith(`${directory}/`) || path === `${directory}/package.json`)
        .sort((a, b) => b.directory.length - a.directory.length)[0];
    if (owner) selected.add(owner.data.name);
}

let expanded = true;
while (expanded) {
    expanded = false;
    for (const { data } of manifests) {
        if (selected.has(data.name)) continue;
        const dependencies = {
            ...data.dependencies,
            ...data.devDependencies,
            ...data.optionalDependencies,
            ...data.peerDependencies,
        };
        if (Object.keys(dependencies).some((name) => names.has(name) && selected.has(name))) {
            selected.add(data.name);
            expanded = true;
        }
    }
}

const targets = manifests
    .filter(({ data }) => selected.has(data.name) && data.scripts?.["test:all"])
    .sort((a, b) => a.data.name.localeCompare(b.data.name));

const changedTests = (workspace) => {
    const prefix = `${workspace.directory}/`;
    const files = new Set(staged.filter((path) => path.startsWith(prefix)).map((path) => path.slice(prefix.length)));
    for (const path of files) {
        if (!/\.(py|js|mjs|ts|tsx)$/.test(path) || /(^|\/)tests?\//.test(path)) continue;
        const stem = path.replace(/\.(py|js|mjs|ts|tsx)$/, "");
        for (const extension of ["ts", "tsx", "js", "mjs"]) {
            const candidate = `${stem}.test.${extension}`;
            if (existsSync(join(repositoryRoot, workspace.directory, candidate))) files.add(candidate);
        }
    }
    return [...files]
        .filter(
            (path) =>
                /(^|\/)test_[^/]+\.py$/.test(path) ||
                /\.test\.(ts|tsx|js|mjs)$/.test(path) ||
                /(^|\/)e2e\/[^/]+\.spec\.(ts|tsx|js|mjs)$/.test(path),
        )
        .sort();
};

const plan = targets.map((workspace) => ({ workspace, tests: changedTests(workspace) }));

if (plan.length === 0) {
    console.log("No staged workspace changes with automated tests.");
    process.exit(0);
}

for (const { workspace, tests } of plan) {
    const fallback =
        workspace.data.devDependencies?.["@playwright/test"] && process.env.WAVEQUANT_COMMIT_E2E !== "1"
            ? "test:unit"
            : "test:all";
    const shown =
        process.env.WAVEQUANT_COMMIT_E2E === "1"
            ? tests
            : tests.filter((path) => !/(^|\/)e2e\/[^/]+\.spec\.(ts|tsx|js|mjs)$/.test(path));
    console.log(`${workspace.data.name}: ${tests.length ? shown.join(", ") || "browser E2E deferred" : fallback}`);
}
if (process.argv.includes("--plan")) process.exit(0);

const pnpmCli = process.env.npm_execpath;
if (!pnpmCli) throw new Error("Run commit tests with pnpm test:commit.");
const browserE2EEnabled = process.env.WAVEQUANT_COMMIT_E2E === "1";
const run = (command, args, cwd, environment = process.env) => {
    const result = spawnSync(command, args, { cwd, env: environment, stdio: "inherit" });
    if (result.error) throw result.error;
    if (result.status !== 0) process.exit(result.status ?? 1);
};
const pnpm = (args, cwd = repositoryRoot, environment = process.env) =>
    run(process.execPath, [pnpmCli, ...args], cwd, environment);
const freePort = () =>
    new Promise((resolvePort, rejectPort) => {
        const server = createServer();
        server.once("error", rejectPort);
        server.listen(0, "127.0.0.1", () => {
            const address = server.address();
            server.close(() => resolvePort(address.port));
        });
    });

pnpm(["exec", "turbo", "run", "build", "--concurrency=1", ...targets.map(({ data }) => `--filter=${data.name}`)]);
for (const { workspace, tests } of plan) {
    const cwd = join(repositoryRoot, workspace.directory);
    if (tests.length === 0) {
        const fallback =
            workspace.data.devDependencies?.["@playwright/test"] && !browserE2EEnabled ? "test:unit" : "test:all";
        pnpm([fallback], cwd);
        continue;
    }
    const python = tests.filter((path) => /(^|\/)test_[^/]+\.py$/.test(path));
    const node = tests.filter((path) => /\.test\.(js|mjs)$/.test(path));
    const vitest = tests.filter((path) => /\.test\.(ts|tsx)$/.test(path));
    const e2e = tests.filter((path) => /(^|\/)e2e\/[^/]+\.spec\.(ts|tsx|js|mjs)$/.test(path));
    if (python.length) run(process.execPath, ["scripts/run-python.mjs", "-m", "pytest", ...python], cwd);
    if (node.length) run(process.execPath, ["--test", ...node], cwd);
    if (vitest.length) pnpm(["exec", "vitest", "run", ...vitest], cwd);
    if (e2e.length && !browserE2EEnabled) {
        console.log(`Browser E2E deferred: ${e2e.join(", ")}`);
    }
    if (e2e.length && browserE2EEnabled) {
        const webPort = await freePort();
        let apiPort = await freePort();
        while (apiPort === webPort) apiPort = await freePort();
        const environment = {
            ...process.env,
            WAVEQUANT_WEB_PORT: String(webPort),
            WAVEQUANT_E2E_WEB_PORT: String(webPort),
            WAVEQUANT_API_PORT: String(apiPort),
        };
        console.log(`Isolated E2E ports: Web ${webPort}, API ${apiPort}`);
        pnpm(["test:e2e", ...e2e], cwd, environment);
    }
}
