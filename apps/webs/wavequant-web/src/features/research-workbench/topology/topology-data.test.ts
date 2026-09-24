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
];

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
        const digest = createHash("sha256");
        for (const file of files) {
            digest.update(file);
            digest.update(readFileSync(resolve(projectRoot, file)));
        }
        expect(digest.digest("hex")).toBe(strategySourceDigest);
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
});
