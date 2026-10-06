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
    "packages/wavequant-core/src/wavequant/domain/market_state",
];
const additionalSources = [
    "packages/wavequant-core/src/wavequant/application/analytics/backtest.py",
    "packages/wavequant-core/src/wavequant/application/analytics/intraday_wave_exit.py",
    "packages/wavequant-core/src/wavequant/application/trading/intent_execution.py",
    "packages/wavequant-core/src/wavequant/domain/models/model.py",
    "packages/wavequant-core/src/wavequant/infrastructure/market_data/data.py",
    "packages/wavequant-core/src/wavequant/infrastructure/market_data/minute.py",
    "apps/webs/wavequant-web/public/wave-entry-evidence.js",
    "apps/webs/wavequant-web/public/combined-a-entry-evidence.js",
    "apps/webs/wavequant-web/public/a-wave-rules.js",
    "apps/webs/wavequant-web/public/confirmed-c-wave.js",
    "apps/webs/wavequant-web/public/ordinary-c-wave.js",
    "apps/webs/wavequant-web/public/structural-c-wave.js",
    "apps/webs/wavequant-web/public/wave-c-projection.js",
    "apps/webs/wavequant-web/public/c-wave-extension.js",
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
    it("requires the actual next response on post-five-top reattacks across entry channels", () => {
        const gate = topologyFlows.flatMap((flow) => flow.gates).find((gate) => gate.id === "five-top-rebreak");
        expect(gate?.detail).toMatch(/首次直接强势站上.*3%.*60%.*20%/);
        expect(gate?.detail).toMatch(/当笔即使强势也等待次笔.*下一实际交易日.*虚拟低点/);
        expect(gate?.detail).toMatch(/第三笔不能回填.*普通正 N、C 波、组合 A、浅回撤和加仓共用门禁/);
        expect(gate?.detail).toMatch(/其他 N 的轧空标签不能绕过.*低于旧五顶仍沿用原回调门禁/);
        expect(gate?.no).toContain("wave_five_top_rebreak_response_pending");
    });
    it("distinguishes shared-boundary outside C from strict mother-candle geometry", () => {
        const gate = topologyFlows.flatMap((flow) => flow.gates).find((gate) => gate.id === "n-geometry");
        expect(gate?.detail).toMatch(/讲义因果外包 C.*等低创新高.*等高创新低/);
        expect(gate?.detail).toMatch(/两侧完全相等不算外包/);
        expect(gate?.detail).toMatch(/C 在自身收盘确认.*分步转折.*实体.*攻击方向.*B 与 C.*同一收盘/);
        expect(gate?.detail).toMatch(/中间其他棒仍检查/);
        expect(gate?.detail).toMatch(/阳母同日 A\/B.*阴母 A < B = C.*独立严格外包/);
        expect(gate?.detail).toMatch(/等高、等低和十字星不放宽该分支.*默认严格 N 条件保持/);
    });
    it("keeps a wick-only outside C pending until a fresh post-C joint attack", () => {
        const gate = topologyFlows.flatMap((flow) => flow.gates).find((gate) => gate.id === "n-attack");
        expect(gate?.detail).toMatch(/收盘严格越过 B 收盘.*最高严格越过 B 最高.*倒 N 对称/);
        expect(gate?.detail).toMatch(/C 当天须收盘严格越过 B 两层.*前收盘未越过 B 收盘/);
        expect(gate?.detail).toMatch(/C 只有影线.*保持形成中.*C 后第一棒.*前收盘重新严格攻击/);
        expect(gate?.detail).toMatch(/后续收盘无需另加越过 B 极值的门槛/);
        expect(gate?.detail).toMatch(/持续站上、相等触及、跨棒拼接或未来才确认.*不能补认/);
        expect(gate?.yes).toMatch(/冻结攻击与防守位/);
    });
    it("traces the post-two-T body reversal and strict previous bearish volume clear", () => {
        const gate = topologyFlows.flatMap((flow) => flow.gates).find((gate) => gate.id === "two-t-body-clear");
        expect(gate?.detail).toMatch(/此前交易日已到二饱/);
        expect(gate?.detail).toMatch(/开盘 ≥ 前阳线收盘.*收盘 ≤ 前阳线开盘.*至少一端严格/);
        expect(gate?.detail).toMatch(/严格大于此前最近阴线.*十字线/);
        expect(gate?.detail).toMatch(/整仓退出优先于减仓.*取消同日新买与加仓/);
    });
    it("traces the post-five-top gap and long upper-shadow clear including doji candles", () => {
        const flow = topologyFlows.find((flow) => flow.id === "exit");
        const gate = flow?.gates.find((gate) => gate.id === "five-top-gap-upper-shadow-clear");
        expect(gate?.detail).toMatch(/此前交易日已达到五顶或十满/);
        expect(gate?.detail).toMatch(/今日开收实体区间被昨日开收实体区间包含.*至少一侧严格.*今日十字实体/);
        expect(gate?.detail).toMatch(/今日最高可以高于昨日最高.*100%清仓.*不要求低开、收阴或放量/);
        expect(gate?.detail).toMatch(/两个实体完全相等不算母子.*保留此前低开路径/);
        expect(gate?.detail).toMatch(/开盘 < 前收.*收盘 ≤ 开盘.*包含十字线/);
        expect(gate?.detail).toMatch(/最高价减去开收较高值.*正振幅至少 50%.*相等允许/);
        expect(gate?.detail).toMatch(/无需放量或此前减仓.*当日达到、未来或已失效目标不能触发/);
        expect(gate?.detail).toMatch(/既有全清.*原退出原因及对应目标证据/);
        expect(gate?.detail).toMatch(/整仓退出优先于减仓.*退出确认后阻止当日后续新买与加仓/);
        expect(gate?.detail).toMatch(/确认前已发生的交易及费用保留/);
        expect(gate?.detail).toMatch(/已完成五分钟线的累计日内 OHLC.*下一根五分钟线开盘.*当刻已观察到的交易权限/);
        expect(gate?.detail).toMatch(
            /缺少完整分钟.*日线收盘.*非一字跌停.*原始收盘价、零滑点.*未验证排队.*一字跌停不放行/,
        );
        expect(gate?.yes).toMatch(/100%.*分钟或日线回退撮合/);
        expect(gate?.detail).toMatch(/来源证明曾在跌停价以上成交.*不要求普通收盘可卖标志为真/);
        expect(gate?.detail).toMatch(/已观察到跌停打开.*下一棒跌停开盘.*零滑点.*未验证排队.*不使用当日日线未来高低/);
        expect(flow?.gates.findIndex((gate) => gate.id === "five-top-gap-upper-shadow-clear")).toBeLessThan(
            flow?.gates.findIndex((gate) => gate.id === "partial") ?? -1,
        );
    });
    it("keeps A classification and lifetime distinct from B squeeze defense", () => {
        const details = topologyFlows
            .flatMap((flow) => flow.gates)
            .map((gate) => gate.detail)
            .join("\n");
        expect(details).toMatch(/一饱.*≤.*A 高点.*<.*二吐/);
        expect(details).toMatch(/二吐.*强势|强势.*二吐/);
        expect(details).toMatch(/最低价.*严格.*A 起点/);
        expect(details).toMatch(/B.*跌破轧空低/);
        expect(details).toMatch(/失效.*新 A/);
    });
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
        const regimeSource = sources.findIndex((source) => source.file.endsWith("/market_state/market_regime.py"));
        expect(regimeSource).toBeGreaterThanOrEqual(0);
        expect(sourceDigest(sources)).toBe(strategySourceDigest);
        expect(
            sourceDigest(sources.map(({ file, content }) => ({ file, content: content.replace(/\r?\n/g, "\r\n") }))),
        ).toBe(strategySourceDigest);
        expect(
            sourceDigest(
                sources.map((source, index) => (index === 0 ? { ...source, content: source.content + " " } : source)),
            ),
        ).not.toBe(strategySourceDigest);
        expect(
            sourceDigest(
                sources.map((source, index) =>
                    index === regimeSource ? { ...source, content: source.content + " " } : source,
                ),
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
