import { existsSync, readFileSync, readdirSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const repositoryRoot = join(scriptDirectory, "..", "..");
const failures = [];

const readJson = (filePath) => JSON.parse(readFileSync(filePath, "utf8"));

const featureStatePath = join(repositoryRoot, "feature_list.json");
const featureState = readJson(featureStatePath);
const features = Array.isArray(featureState.features) ? featureState.features : [];
const featureIds = new Set(features.map((feature) => feature.id));
const inProgressFeatures = features.filter((feature) => feature.status === "in-progress");

if (featureState.schemaVersion !== 1) failures.push("feature_list.json schemaVersion 必须为 1");
if (featureIds.size !== features.length) failures.push("feature id 必须全仓唯一");
if (inProgressFeatures.length > 1) failures.push("同一时间只允许一个 in-progress 功能");

if (featureState.activeFeature === null && inProgressFeatures.length !== 0) {
    failures.push("activeFeature 为空时不能存在 in-progress 功能");
}

if (featureState.activeFeature !== null) {
    const activeFeature = features.find((feature) => feature.id === featureState.activeFeature);
    if (!activeFeature || activeFeature.status !== "in-progress") {
        failures.push("activeFeature 必须指向唯一的 in-progress 功能");
    }
}

for (const feature of features) {
    if (!feature.id || !feature.title || !feature.scope) failures.push("每个功能必须包含 id、title 和 scope");
    if (!Array.isArray(feature.acceptanceCriteria) || feature.acceptanceCriteria.length === 0) {
        failures.push(`${feature.id ?? "未知功能"} 必须包含验收条件`);
    }
    for (const dependency of feature.dependencies ?? []) {
        if (!featureIds.has(dependency)) failures.push(`${feature.id} 引用了不存在的依赖 ${dependency}`);
    }
    if (feature.status === "done") {
        const verification = feature.verification ?? [];
        if (verification.length === 0 || verification.some((item) => item.status !== "passed")) {
            failures.push(`${feature.id} 标记为 done 前必须记录全部通过的验证证据`);
        }
    }
}

const roadmapPath = join(repositoryRoot, "ROADMAP.md");
if (!existsSync(roadmapPath)) {
    failures.push("缺少 ROADMAP.md");
} else {
    const roadmap = readFileSync(roadmapPath, "utf8");
    const requiredRoadmapHeadings = [
        "# 项目路线图",
        "## 项目方向",
        "## 如何阅读",
        "## 现在（Now）",
        "## 下一步（Next）",
        "## 未来（Later）",
        "## 已交付基础",
        "## 贡献与推进流程",
    ];

    for (const heading of requiredRoadmapHeadings) {
        if (!roadmap.includes(heading)) failures.push(`ROADMAP.md 缺少章节 ${heading}`);
    }

    if (!roadmap.includes("feature_list.json")) {
        failures.push("ROADMAP.md 必须声明 feature_list.json 的执行事实来源边界");
    }

    const roadmapFeatureIds = new Set(roadmap.match(/\bMONOREPO-\d{3}\b/g) ?? []);
    for (const featureId of roadmapFeatureIds) {
        if (!featureIds.has(featureId)) failures.push(`ROADMAP.md 引用了不存在的 Feature ${featureId}`);
    }
}

const workspacePackagePaths = [];
const collectPackages = (directory, remainingDepth) => {
    if (!existsSync(directory) || remainingDepth < 0) return;
    for (const entry of readdirSync(directory, { withFileTypes: true })) {
        if (!entry.isDirectory() || ["node_modules", ".git", ".next", "dist", "coverage"].includes(entry.name))
            continue;
        const childDirectory = join(directory, entry.name);
        const packagePath = join(childDirectory, "package.json");
        if (existsSync(packagePath)) workspacePackagePaths.push(packagePath);
        collectPackages(childDirectory, remainingDepth - 1);
    }
};

collectPackages(join(repositoryRoot, "apps"), 2);
collectPackages(join(repositoryRoot, "packages"), 2);

const progressHeadings = [
    "# Progress",
    "## Current State",
    "## Completed",
    "## Verification",
    "## Risks and Next Steps",
];
for (const packagePath of workspacePackagePaths) {
    const workspaceDirectory = dirname(packagePath);
    const progressPath = join(workspaceDirectory, "progress.md");
    if (!existsSync(progressPath)) {
        failures.push(`${relative(repositoryRoot, workspaceDirectory)} 缺少 progress.md`);
        continue;
    }
    const progress = readFileSync(progressPath, "utf8");
    for (const heading of progressHeadings) {
        if (!progress.includes(heading)) failures.push(`${relative(repositoryRoot, progressPath)} 缺少章节 ${heading}`);
    }
}

const handoffPath = join(repositoryRoot, "session-handoff.md");
if (!existsSync(handoffPath)) failures.push("缺少 session-handoff.md");

const requiredAgentRules = [
    "docs/agent/README.md",
    "docs/agent/general/rules.md",
    "docs/agent/general/quality-rules.md",
    "docs/agent/frontend/rules.md",
    "docs/agent/frontend/pc-web-rules.md",
    "docs/agent/backend/rules.md",
    "docs/agent/backend/nestjs-rules.md",
    "docs/agent/backend/architecture-rules.md",
    "docs/agent/backend/api-rules.md",
    "docs/agent/backend/data-rules.md",
    "docs/agent/backend/reliability-rules.md",
    "docs/agent/backend/security-rules.md",
    "docs/agent/python/rules.md",
    "docs/agent/python/testing-rules.md",
    "docs/agent/python/packaging-rules.md",
    "docs/agent/architecture/rules.md",
    "docs/agent/architecture/system-design-rules.md",
    "docs/agent/architecture/distributed-systems-rules.md",
    "docs/agent/data-warehouse/rules.md",
    "docs/agent/data-warehouse/tracking-rules.md",
    "docs/agent/data-warehouse/modeling-governance-rules.md",
    "docs/agent/data-warehouse/analytics-experiment-rules.md",
    "docs/agent/algorithms/rules.md",
    "docs/agent/operations/rules.md",
    "docs/agent/operations/delivery-rules.md",
    "docs/agent/operations/infrastructure-rules.md",
    "docs/agent/operations/observability-incident-rules.md",
    "docs/agent/ai/rules.md",
    "docs/agent/ai/prompt-context-rules.md",
    "docs/agent/ai/rag-retrieval-rules.md",
    "docs/agent/ai/agent-tool-rules.md",
    "docs/agent/ai/model-delivery-rules.md",
];

for (const rulePath of requiredAgentRules) {
    if (!existsSync(join(repositoryRoot, ...rulePath.split("/")))) {
        failures.push(`缺少 Agent 规则 ${rulePath}`);
    }
}

const agentMarkdownPaths = [join(repositoryRoot, "AGENTS.md")];
const collectMarkdown = (directory) => {
    for (const entry of readdirSync(directory, { withFileTypes: true })) {
        const entryPath = join(directory, entry.name);
        if (entry.isDirectory()) collectMarkdown(entryPath);
        else if (entry.isFile() && entry.name.endsWith(".md")) agentMarkdownPaths.push(entryPath);
    }
};
collectMarkdown(join(repositoryRoot, "docs", "agent"));

const markdownReferencePattern = /`((?:docs\/agent|apps|packages)\/[^`\n]+\.md)`/g;
for (const markdownPath of agentMarkdownPaths) {
    const markdown = readFileSync(markdownPath, "utf8");
    for (const match of markdown.matchAll(markdownReferencePattern)) {
        if (match[1].includes("<")) continue;
        const referencedPath = join(repositoryRoot, ...match[1].split("/"));
        if (!existsSync(referencedPath)) {
            failures.push(`${relative(repositoryRoot, markdownPath)} 引用了不存在的 ${match[1]}`);
        }
    }
}

if (failures.length > 0) {
    console.error("Harness validation failed:\n" + failures.map((failure) => `- ${failure}`).join("\n"));
    process.exit(1);
}

console.log(
    `Harness validation passed: ${features.length} feature(s), ${workspacePackagePaths.length} workspace(s), ${requiredAgentRules.length} agent rule(s).`,
);
