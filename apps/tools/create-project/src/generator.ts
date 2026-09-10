import { access, cp, mkdir, readFile, readdir, rename, rm, writeFile } from "node:fs/promises";
import { basename, dirname, isAbsolute, join, relative, resolve, sep } from "node:path";

import { templateDefinitions } from "./templates.js";
import type { ICreateProjectOptions, ICreateProjectResult, IProjectPlan } from "./types.js";

const excludedDirectoryNames = new Set([
    ".expo",
    ".git",
    ".next",
    ".turbo",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "out",
    "playwright-report",
    "test-results",
]);

const projectNamePattern = /^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$/;
const minimumPort = 1024;
const maximumPort = 65_535;

interface IPackageJson {
    name?: string;
    description?: string;
    scripts?: Record<string, string>;
    [key: string]: unknown;
}

interface IExpoConfig {
    name?: string;
    slug?: string;
    scheme?: string;
    ios?: Record<string, unknown>;
    android?: Record<string, unknown>;
    [key: string]: unknown;
}

interface IAppJson {
    expo?: IExpoConfig;
    [key: string]: unknown;
}

const pathExists = async (path: string) => {
    try {
        await access(path);
        return true;
    } catch {
        return false;
    }
};

const assertPathInside = (parentPath: string, childPath: string) => {
    const resolvedParent = resolve(parentPath);
    const resolvedChild = resolve(childPath);
    if (resolvedChild === resolvedParent || !resolvedChild.startsWith(`${resolvedParent}${sep}`)) {
        throw new Error(`目标路径必须位于 ${resolvedParent} 内`);
    }
};

export const validateProjectName = (name: string) => {
    const normalizedName = name.trim();
    if (!projectNamePattern.test(normalizedName)) {
        throw new Error("项目名必须使用 kebab-case，并以小写字母开头");
    }
    return normalizedName;
};

const normalizePort = (port: number | undefined) => {
    if (port === undefined) return undefined;
    if (!Number.isInteger(port) || port < minimumPort || port > maximumPort) {
        throw new Error(`端口必须是 ${minimumPort}-${maximumPort} 之间的整数`);
    }
    return port;
};

const readJson = async <TValue>(path: string) => JSON.parse(await readFile(path, "utf8")) as TValue;

const writeJson = async (path: string, value: unknown) =>
    writeFile(path, `${JSON.stringify(value, null, 4)}\n`, "utf8");

const listPackageJsonPaths = async (directory: string): Promise<string[]> => {
    if (!(await pathExists(directory))) return [];

    const paths: string[] = [];
    for (const entry of await readdir(directory, { withFileTypes: true })) {
        if (!entry.isDirectory() || excludedDirectoryNames.has(entry.name)) continue;
        const childPath = join(directory, entry.name);
        const packageJsonPath = join(childPath, "package.json");
        if (await pathExists(packageJsonPath)) paths.push(packageJsonPath);
        paths.push(...(await listPackageJsonPaths(childPath)));
    }
    return paths;
};

const readWorkspaceNames = async (repositoryRoot: string) => {
    const packagePaths = [
        ...(await listPackageJsonPaths(join(repositoryRoot, "apps"))),
        ...(await listPackageJsonPaths(join(repositoryRoot, "packages"))),
    ];
    const names = new Set<string>();
    for (const packagePath of packagePaths) {
        const packageJson = await readJson<IPackageJson>(packagePath);
        if (packageJson.name) names.add(packageJson.name);
    }
    return names;
};

const extractPorts = (packageJson: IPackageJson) => {
    const ports = new Set<number>();
    for (const scriptName of ["dev", "start"] as const) {
        const script = packageJson.scripts?.[scriptName];
        if (!script || !/\bnext\s+(?:dev|start)\b/.test(script)) continue;
        const match = script.match(/--port(?:=|\s+)(\d+)/);
        ports.add(match ? Number(match[1]) : 3000);
    }
    return ports;
};

const readUsedWebPorts = async (repositoryRoot: string) => {
    const packagePaths = await listPackageJsonPaths(join(repositoryRoot, "apps", "webs"));
    const ports = new Set<number>();
    for (const packagePath of packagePaths) {
        const packageJson = await readJson<IPackageJson>(packagePath);
        for (const port of extractPorts(packageJson)) ports.add(port);
    }
    return ports;
};

const chooseWebPort = (usedPorts: ReadonlySet<number>) => {
    for (let port = 3000; port <= maximumPort; port += 1) {
        if (!usedPorts.has(port)) return port;
    }
    throw new Error("没有可用的 H5/Admin 端口");
};

export const findRepositoryRoot = async (startDirectory = process.cwd()) => {
    let currentDirectory = resolve(startDirectory);
    while (true) {
        if (
            (await pathExists(join(currentDirectory, "pnpm-workspace.yaml"))) &&
            (await pathExists(join(currentDirectory, "apps")))
        ) {
            return currentDirectory;
        }
        const parentDirectory = dirname(currentDirectory);
        if (parentDirectory === currentDirectory) {
            throw new Error("无法找到包含 pnpm-workspace.yaml 和 apps 的仓库根目录");
        }
        currentDirectory = parentDirectory;
    }
};

export const createProjectPlan = async (options: ICreateProjectOptions): Promise<IProjectPlan> => {
    const repositoryRoot = resolve(options.repositoryRoot);
    const template = templateDefinitions[options.template];
    const name = validateProjectName(options.name);
    const sourcePath = resolve(repositoryRoot, template.sourceDirectory);
    const targetRoot = resolve(repositoryRoot, template.targetDirectory);
    const targetPath = resolve(targetRoot, name);

    assertPathInside(repositoryRoot, sourcePath);
    assertPathInside(targetRoot, targetPath);

    if (!(await pathExists(sourcePath))) throw new Error(`模板目录不存在：${template.sourceDirectory}`);
    if (await pathExists(targetPath)) throw new Error(`目标目录已存在：${relative(repositoryRoot, targetPath)}`);

    const workspaceNames = await readWorkspaceNames(repositoryRoot);
    if (workspaceNames.has(name) || workspaceNames.has(`@repo/${name}`)) {
        throw new Error(`workspace 名称已存在：${name}`);
    }

    let port: number | undefined;
    if (template.kind === "web") {
        const usedPorts = await readUsedWebPorts(repositoryRoot);
        port = normalizePort(options.port) ?? chooseWebPort(usedPorts);
        if (usedPorts.has(port)) throw new Error(`H5/Admin 端口已被其他 workspace 使用：${port}`);
    } else if (options.port !== undefined) {
        throw new Error(`模板 ${template.id} 不支持 --port`);
    }

    return {
        repositoryRoot,
        template,
        name,
        sourcePath,
        targetPath,
        targetRelativePath: relative(repositoryRoot, targetPath).replaceAll(sep, "/"),
        ...(port === undefined ? {} : { port }),
    };
};

const shouldCopy = (sourceRoot: string, sourcePath: string) => {
    const relativePath = relative(sourceRoot, sourcePath);
    if (!relativePath) return true;
    const segments = relativePath.split(sep);
    if (segments.some((segment) => excludedDirectoryNames.has(segment))) return false;

    const fileName = basename(sourcePath);
    if (fileName === ".DS_Store") return false;
    if (fileName === ".env.example" || (fileName.startsWith(".env.") && fileName.endsWith(".example"))) {
        return true;
    }
    return fileName !== ".env" && !fileName.startsWith(".env.");
};

const withPort = (script: string, port: number) =>
    `${script.replace(/\s+--port(?:=|\s+)\d+\b/g, "").trim()} --port ${port}`;

const updatePackageJson = async (plan: IProjectPlan, temporaryPath: string) => {
    const packageJsonPath = join(temporaryPath, "package.json");
    const packageJson = await readJson<IPackageJson>(packageJsonPath);
    packageJson.name = plan.name;
    packageJson.description = `${plan.template.label} project generated from the monorepo template`;

    if (plan.template.kind === "web" && plan.port !== undefined && packageJson.scripts) {
        for (const scriptName of ["dev", "start"] as const) {
            const script = packageJson.scripts[scriptName];
            if (script) packageJson.scripts[scriptName] = withPort(script, plan.port);
        }
    }

    await writeJson(packageJsonPath, packageJson);
};

const toDisplayName = (name: string) =>
    name
        .split("-")
        .map((part) => `${part.charAt(0).toUpperCase()}${part.slice(1)}`)
        .join(" ");

const updateExpoConfig = async (plan: IProjectPlan, temporaryPath: string) => {
    if (plan.template.kind !== "mobile") return;
    const appJsonPath = join(temporaryPath, "app.json");
    if (!(await pathExists(appJsonPath))) return;

    const appJson = await readJson<IAppJson>(appJsonPath);
    if (!appJson.expo) throw new Error("Mobile 模板缺少 expo 配置");

    const identifierSuffix = plan.name.replaceAll("-", "");
    appJson.expo.name = toDisplayName(plan.name);
    appJson.expo.slug = plan.name;
    appJson.expo.scheme = plan.name;
    appJson.expo.ios = {
        ...appJson.expo.ios,
        bundleIdentifier: `com.template.${identifierSuffix}`,
    };
    appJson.expo.android = {
        ...appJson.expo.android,
        package: `com.template.${identifierSuffix}`,
    };
    await writeJson(appJsonPath, appJson);
};

const createPlaywrightConfig = (port: number) => `import { defineConfig } from "@playwright/test";
import { baseConfig } from "@repo/playwright";

export default defineConfig({
    ...baseConfig,
    outputDir: "./test-results",
    use: {
        ...baseConfig.use,
        baseURL: "http://localhost:${port}",
        screenshot: "only-on-failure",
    },
    webServer: {
        command: "pnpm dev",
        url: "http://localhost:${port}",
        reuseExistingServer: !process.env.CI,
    },
});
`;

const updateDockerfile = async (plan: IProjectPlan, temporaryPath: string) => {
    const dockerfilePath = join(temporaryPath, "Dockerfile");
    if (!(await pathExists(dockerfilePath))) return;

    let contents = await readFile(dockerfilePath, "utf8");
    contents = contents
        .replaceAll(plan.template.sourceDirectory, plan.targetRelativePath)
        .replaceAll(`--filter="${plan.template.id}..."`, `--filter="${plan.name}..."`)
        .replaceAll(`"--filter", "${plan.template.id}"`, `"--filter", "${plan.name}"`);

    if (plan.template.kind === "web" && plan.port !== undefined) {
        contents = contents.replaceAll("3000", String(plan.port));
    }
    await writeFile(dockerfilePath, contents, "utf8");
};

const createReadme = (plan: IProjectPlan) => `# ${toDisplayName(plan.name)}

Generated from the \`${plan.template.id}\` template by \`@repo/create-project\`.

## Getting started

From the repository root:

\`\`\`bash
pnpm install
pnpm --filter ${plan.name} ${plan.template.startScript}
\`\`\`

## Quality checks

\`\`\`bash
pnpm --filter ${plan.name} lint
pnpm --filter ${plan.name} typecheck
pnpm --filter ${plan.name} test:unit
pnpm --filter ${plan.name} build
\`\`\`

Review and replace all example content, metadata, identifiers and environment placeholders before production use.
`;

const createProgress = (plan: IProjectPlan) => `# Progress

## Current State

\`${plan.name}\` 已从 \`${plan.template.id}\` 模板生成，尚未进行业务定制。

## Completed

- 已在 \`${plan.targetRelativePath}\` 创建 workspace。
- 已替换 workspace 元数据和模板相关运行标识。

## Verification

- 生成后尚未验证；安装依赖后需执行 workspace 质量门禁。

## Risks and Next Steps

- 替换示例内容、标识、环境变量占位符和领域行为。
- 通过 lint、类型检查、单元测试和构建后才能标记项目可用。
`;

const updateGeneratedProject = async (plan: IProjectPlan, temporaryPath: string) => {
    await updatePackageJson(plan, temporaryPath);
    await updateExpoConfig(plan, temporaryPath);
    await updateDockerfile(plan, temporaryPath);
    if (plan.template.kind === "web" && plan.port !== undefined) {
        await writeFile(join(temporaryPath, "playwright.config.ts"), createPlaywrightConfig(plan.port), "utf8");
    }
    await writeFile(join(temporaryPath, "README.md"), createReadme(plan), "utf8");
    await writeFile(join(temporaryPath, "progress.md"), createProgress(plan), "utf8");
};

const countFiles = async (directory: string): Promise<number> => {
    let count = 0;
    for (const entry of await readdir(directory, { withFileTypes: true })) {
        const entryPath = join(directory, entry.name);
        count += entry.isDirectory() ? await countFiles(entryPath) : 1;
    }
    return count;
};

export const createProject = async (options: ICreateProjectOptions): Promise<ICreateProjectResult> => {
    const plan = await createProjectPlan(options);
    if (options.dryRun) return { ...plan, created: false, copiedFileCount: 0 };

    const targetRoot = dirname(plan.targetPath);
    const temporaryPath = join(targetRoot, `.${plan.name}.create-${process.pid}-${Date.now()}`);
    assertPathInside(targetRoot, temporaryPath);
    await mkdir(targetRoot, { recursive: true });

    try {
        await cp(plan.sourcePath, temporaryPath, {
            recursive: true,
            errorOnExist: true,
            force: false,
            filter: (sourcePath) => shouldCopy(plan.sourcePath, sourcePath),
        });
        await updateGeneratedProject(plan, temporaryPath);
        const copiedFileCount = await countFiles(temporaryPath);
        await rename(temporaryPath, plan.targetPath);
        return { ...plan, created: true, copiedFileCount };
    } catch (error) {
        if (isAbsolute(temporaryPath) && temporaryPath.startsWith(`${targetRoot}${sep}`)) {
            await rm(temporaryPath, { recursive: true, force: true });
        }
        throw error;
    }
};
