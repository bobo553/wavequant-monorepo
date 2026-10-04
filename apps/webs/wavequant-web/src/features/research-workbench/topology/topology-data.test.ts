import { createHash } from "node:crypto";
import { readFileSync, readdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { strategySourceDigest, topologyFlows } from "./topology-data";

const projectRoot = resolve(dirname(fileURLToPath(import.meta.url)), "../../../../../../..");
const strategyDirectories = [
    "packages/wavequant-core/src/wavequant/domain/strategies",
    "packages/wavequant-core/src/wavequant/domain/market_structure",
];
const additionalSources = [
    "packages/wavequant-core/src/wavequant/application/analytics/backtest.py",
    "packages/wavequant-core/src/wavequant/application/trading/intent_execution.py",
    "apps/webs/wavequant-web/public/wave-entry-evidence.js",
    "apps/webs/wavequant-web/public/combined-a-entry-evidence.js",
];

function sourceDigest(sources: { file: string; content: string }[]): string {
    const digest = createHash("sha256");
    for (const { file, content } of sources) {
        digest.update(file);
        // Git 的平台换行转换不改变拓扑版本；其他源码内容仍需重新复核。
        digest.update(content.replace(/\r\n/g, "\n"));
    }
    return digest.digest("hex");
}

describe("strategy topology", () => {
    it("requires a diagram review whenever strategy source changes", () => {
        const files = [
            ...strategyDirectories.flatMap((directory) =>
                readdirSync(resolve(projectRoot, directory))
                    .filter((file) => file.endsWith(".py"))
                    .map((file) => `${directory}/${file}`),
            ),
            ...additionalSources,
        ].sort();
        const sources = files.map((file) => ({ file, content: readFileSync(resolve(projectRoot, file), "utf8") }));
        expect(sourceDigest(sources)).toBe(strategySourceDigest);
        expect(
            sourceDigest(sources.map(({ file, content }) => ({ file, content: content.replace(/\r?\n/g, "\r\n") }))),
        ).toBe(strategySourceDigest);
        expect(
            sourceDigest(
                sources.map((source, index) => (index === 0 ? { ...source, content: source.content + " " } : source)),
            ),
        ).not.toBe(strategySourceDigest);
    });

    it("provides explicit yes and no routes for every decision", () => {
        for (const flow of topologyFlows) {
            const ids = new Set(flow.gates.map((gate) => gate.id));
            expect(ids.size).toBe(flow.gates.length);
            expect(flow.completion.length).toBeGreaterThan(0);
            for (const gate of flow.gates) {
                expect(gate.question.length).toBeGreaterThan(0);
                expect(gate.detail.length).toBeGreaterThan(0);
                expect(gate.source.length).toBeGreaterThan(0);
                expect(gate.yes.length).toBeGreaterThan(0);
                expect(gate.no.length).toBeGreaterThan(0);
                if (gate.yesNext) expect(ids.has(gate.yesNext)).toBe(true);
                if (gate.noNext) expect(ids.has(gate.noNext)).toBe(true);
            }
        }
    });

    it("explains the independent combined A duration, close defense and dated breakout path", () => {
        const flow = topologyFlows.find((entry) => entry.id === "combined-a-breakout");
        expect(flow).toBeDefined();
        const details = flow?.gates.map((gate) => `${gate.question} ${gate.detail}`).join("\n");
        expect(details).toMatch(/回调及整理.*组合内部回调.*或.*子级回调.*满足其一/);
        expect(details).toMatch(/收盘.*2\/3.*相等/);
        expect(details).toMatch(/实体.*3%.*振幅.*60%.*严格大于前日/);
        expect(details).toMatch(/参考高.*可知.*严格突破/);
        expect(flow?.completion).toMatch(/LONG.*执行与成交/);
    });
});
