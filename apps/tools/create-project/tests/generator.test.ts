import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { createProject, createProjectPlan, findRepositoryRoot, validateProjectName } from "../src/generator.js";

const writeJson = (path: string, value: unknown) => writeFile(path, `${JSON.stringify(value, null, 4)}\n`, "utf8");

const createFakeRepository = async () => {
    const repositoryRoot = await mkdtemp(join(tmpdir(), "monorepo-create-project-"));
    const h5TemplatePath = join(repositoryRoot, "apps", "webs", "h5");
    const adminTemplatePath = join(repositoryRoot, "apps", "webs", "admin");
    const apiTemplatePath = join(repositoryRoot, "apps", "servers", "api");
    const mobileTemplatePath = join(repositoryRoot, "apps", "mobiles", "mobile");
    await mkdir(join(h5TemplatePath, "src"), { recursive: true });
    await mkdir(join(h5TemplatePath, ".next"), { recursive: true });
    await mkdir(join(adminTemplatePath, "src"), { recursive: true });
    await mkdir(join(apiTemplatePath, "src"), { recursive: true });
    await mkdir(join(mobileTemplatePath, "src"), { recursive: true });
    await writeFile(join(repositoryRoot, "pnpm-workspace.yaml"), "packages: []\n", "utf8");
    await writeJson(join(h5TemplatePath, "package.json"), {
        name: "h5",
        private: true,
        scripts: { dev: "next dev", start: "next start" },
    });
    await writeFile(join(h5TemplatePath, "src", "index.ts"), "export {};\n", "utf8");
    await writeFile(join(h5TemplatePath, ".env.production"), "SECRET=value\n", "utf8");
    await writeFile(join(h5TemplatePath, ".env.example"), "PUBLIC_VALUE=\n", "utf8");
    await writeFile(join(h5TemplatePath, ".env.development.example"), "DEV_VALUE=\n", "utf8");
    await writeFile(join(h5TemplatePath, ".next", "artifact"), "generated\n", "utf8");
    await writeJson(join(adminTemplatePath, "package.json"), {
        name: "admin",
        private: true,
        scripts: { dev: "next dev --turbopack --port 3002", start: "next start --port 3002" },
    });
    await writeFile(join(adminTemplatePath, "src", "index.ts"), "export {};\n", "utf8");
    await writeJson(join(apiTemplatePath, "package.json"), {
        name: "api",
        private: true,
        scripts: { dev: "nest start --watch", start: "node dist/main" },
    });
    await writeFile(join(apiTemplatePath, "src", "index.ts"), "export {};\n", "utf8");
    await writeFile(
        join(apiTemplatePath, "Dockerfile"),
        'COPY apps/servers/api ./apps/servers/api\nRUN pnpm turbo build --filter="api..."\nCMD ["node", "apps/servers/api/dist/main.js"]\n',
        "utf8",
    );
    await writeJson(join(mobileTemplatePath, "package.json"), {
        name: "mobile",
        private: true,
        scripts: { start: "expo start" },
    });
    await writeJson(join(mobileTemplatePath, "app.json"), {
        expo: {
            name: "Mobile",
            slug: "mobile",
            scheme: "mobile",
            ios: { bundleIdentifier: "com.template.mobile" },
            android: { package: "com.template.mobile" },
        },
    });
    await writeFile(join(mobileTemplatePath, "src", "index.ts"), "export {};\n", "utf8");
    return repositoryRoot;
};

describe("validateProjectName", () => {
    it("accepts a kebab-case workspace name", () => {
        expect(validateProjectName("customer-portal")).toBe("customer-portal");
    });

    it.each(["CustomerPortal", "customer_portal", "../portal", "1-portal"])(
        "rejects unsafe project name %s",
        (name) => {
            expect(() => validateProjectName(name)).toThrow("kebab-case");
        },
    );
});

describe("createProject", () => {
    let repositoryRoot: string;

    beforeEach(async () => {
        repositoryRoot = await createFakeRepository();
    });

    afterEach(async () => {
        await rm(repositoryRoot, { recursive: true, force: true });
    });

    it("finds the repository root from a nested directory", async () => {
        expect(await findRepositoryRoot(join(repositoryRoot, "apps", "webs", "h5", "src"))).toBe(repositoryRoot);
    });

    it("creates an H5 workspace, selects a free port, and excludes unsafe files", async () => {
        const result = await createProject({
            repositoryRoot,
            template: "h5",
            name: "campaign-share",
        });

        expect(result.created).toBe(true);
        expect(result.port).toBe(3001);
        const targetPath = join(repositoryRoot, "apps", "webs", "campaign-share");
        const packageJson = JSON.parse(await readFile(join(targetPath, "package.json"), "utf8")) as {
            name: string;
            scripts: Record<string, string>;
        };
        expect(packageJson.name).toBe("campaign-share");
        expect(packageJson.scripts.dev).toBe("next dev --port 3001");
        expect(packageJson.scripts.start).toBe("next start --port 3001");
        await expect(readFile(join(targetPath, ".env.production"), "utf8")).rejects.toThrow();
        await expect(readFile(join(targetPath, ".next", "artifact"), "utf8")).rejects.toThrow();
        await expect(readFile(join(targetPath, ".env.example"), "utf8")).resolves.toContain("PUBLIC_VALUE");
        await expect(readFile(join(targetPath, ".env.development.example"), "utf8")).resolves.toContain("DEV_VALUE");
        await expect(readFile(join(targetPath, "progress.md"), "utf8")).resolves.toContain("campaign-share");
    });

    it("rewrites Admin scripts and Playwright URLs for an explicit port", async () => {
        await createProject({
            repositoryRoot,
            template: "admin",
            name: "operations-admin",
            port: 3010,
        });

        const targetPath = join(repositoryRoot, "apps", "webs", "operations-admin");
        const packageJson = JSON.parse(await readFile(join(targetPath, "package.json"), "utf8")) as {
            scripts: Record<string, string>;
        };
        expect(packageJson.scripts.dev).toBe("next dev --turbopack --port 3010");
        expect(packageJson.scripts.start).toBe("next start --port 3010");
        await expect(readFile(join(targetPath, "playwright.config.ts"), "utf8")).resolves.toContain(
            "http://localhost:3010",
        );
    });

    it("rewrites API Docker paths and workspace filters", async () => {
        await createProject({
            repositoryRoot,
            template: "api",
            name: "billing-api",
        });

        const dockerfile = await readFile(join(repositoryRoot, "apps", "servers", "billing-api", "Dockerfile"), "utf8");
        expect(dockerfile).toContain("apps/servers/billing-api");
        expect(dockerfile).toContain('--filter="billing-api..."');
        expect(dockerfile).not.toContain("apps/servers/api");
    });

    it("rewrites Expo identifiers for a Mobile workspace", async () => {
        await createProject({
            repositoryRoot,
            template: "mobile",
            name: "field-ops",
        });

        const appJson = JSON.parse(
            await readFile(join(repositoryRoot, "apps", "mobiles", "field-ops", "app.json"), "utf8"),
        ) as {
            expo: {
                name: string;
                slug: string;
                scheme: string;
                ios: { bundleIdentifier: string };
                android: { package: string };
            };
        };
        expect(appJson.expo).toMatchObject({
            name: "Field Ops",
            slug: "field-ops",
            scheme: "field-ops",
            ios: { bundleIdentifier: "com.template.fieldops" },
            android: { package: "com.template.fieldops" },
        });
    });

    it("performs a dry run without creating the target", async () => {
        const result = await createProject({
            repositoryRoot,
            template: "h5",
            name: "preview-h5",
            dryRun: true,
        });

        expect(result.created).toBe(false);
        await expect(
            readFile(join(repositoryRoot, "apps", "webs", "preview-h5", "package.json"), "utf8"),
        ).rejects.toThrow();
    });

    it("rejects an existing target and an occupied Web port", async () => {
        await mkdir(join(repositoryRoot, "apps", "webs", "existing"));
        await expect(createProjectPlan({ repositoryRoot, template: "h5", name: "existing" })).rejects.toThrow(
            "目标目录已存在",
        );
        await expect(createProjectPlan({ repositoryRoot, template: "h5", name: "new-h5", port: 3000 })).rejects.toThrow(
            "端口已被",
        );
    });

    it("rejects a port for templates that do not expose an H5/Admin server", async () => {
        await expect(
            createProjectPlan({ repositoryRoot, template: "mobile", name: "new-mobile", port: 3010 }),
        ).rejects.toThrow("不支持 --port");
    });
});
