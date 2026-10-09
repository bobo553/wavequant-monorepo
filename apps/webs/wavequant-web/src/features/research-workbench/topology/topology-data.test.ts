import { createHash } from "node:crypto";
import { readFileSync, readdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { strategySourceDigest, topologyFlows, topologyProfileNotes } from "./topology-data";

const projectRoot = resolve(dirname(fileURLToPath(import.meta.url)), "../../../../../../..");
const strategyDirectories = [
    "packages/wavequant-core/src/wavequant/domain/strategies",
    "packages/wavequant-core/src/wavequant/domain/market_structure",
    "packages/wavequant-core/src/wavequant/domain/market_state",
];
const additionalSources = [
    "packages/wavequant-core/src/wavequant/application/analytics/backtest.py",
    "packages/wavequant-core/src/wavequant/application/analytics/intraday_entry.py",
    "packages/wavequant-core/src/wavequant/application/analytics/intraday_wave_exit.py",
    "packages/wavequant-core/src/wavequant/application/trading/intent_execution.py",
    "packages/wavequant-core/src/wavequant/interfaces/research_tools/stock_backtest.py",
    "packages/wavequant-core/src/wavequant/domain/models/model.py",
    "packages/wavequant-core/src/wavequant/infrastructure/market_data/data.py",
    "packages/wavequant-core/src/wavequant/infrastructure/market_data/minute.py",
    "apps/webs/wavequant-web/public/wave-entry-evidence.js",
    "apps/webs/wavequant-web/public/combined-a-entry-evidence.js",
    "apps/webs/wavequant-web/public/secondary-reclaim-evidence.js",
    "apps/webs/wavequant-web/public/a-wave-rules.js",
    "apps/webs/wavequant-web/public/a-wave-observations.js",
    "apps/webs/wavequant-web/public/confirmed-c-wave.js",
    "apps/webs/wavequant-web/public/ordinary-c-wave.js",
    "apps/webs/wavequant-web/public/structural-c-wave.js",
    "apps/webs/wavequant-web/public/wave-c-projection.js",
    "apps/webs/wavequant-web/public/c-wave-extension.js",
    "apps/webs/wavequant-web/public/bottom-n-targets.js",
    "apps/webs/wavequant-web/public/n-target-focus.js",
    "apps/webs/wavequant-web/public/n-structure-overlay.js",
    "apps/webs/wavequant-web/public/charts.js",
    "apps/webs/wavequant-web/public/target-level-guides.js",
    "apps/webs/wavequant-web/public/buy-n-targets.js",
    "apps/webs/wavequant-web/public/annotations.js",
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
    it("uses the same complete confirmation routes at all three trend levels", () => {
        const note = topologyProfileNotes.find((text) => text.includes("V3 v107"));
        expect(note).toMatch(/一、二、三级.*本级末跌高.*下一级末跌高突破.*空多交替.*收盘转多/);
        expect(note).toMatch(/缺交替.*不升级.*基础折线.*因果证书/);
        expect(note).toMatch(/本级突破直接升级.*深回调买点保留/);
        expect(note).toMatch(/整段最低价.*局部确认起点.*原低点.*可知日.*不提前/);
    });
    it("separates causal direct promotion from the independent deep B reclaim", () => {
        const flow = topologyFlows.find((item) => item.id === "secondary-pullback");
        expect(flow?.gates).toHaveLength(3);
        expect(flow?.gates[0]?.detail).toMatch(/价格日之前已知.*可知日.*不得早于.*不等待未来/);
        expect(flow?.gates[1]?.detail).toMatch(/前日已知.*至少 2\/3.*基础折线.*不提前升.*不伪造新正 N/);
        expect(flow?.gates[2]?.detail).toMatch(/量严格大于.*2%.*50%.*费用后盈亏比.*无量越线/);
        expect(topologyProfileNotes.find((note) => note.includes("V3 v105"))).toMatch(/实际 B 防守、A 高目标/);
    });
    it("documents high extension and the whole three-in-one stacking box without granting an automatic buy", () => {
        const note = topologyProfileNotes.find((text) => text.includes("V3 v104"));
        expect(note).toMatch(/二吐.*未拉回.*最高价严格创新高.*不等待收盘创高/);
        expect(note).toMatch(/三合一整箱.*底部到二吐的 3H.*二吐加 3H/);
        expect(note).toMatch(/新发布目标当日只用收盘.*不倒用同根最高价/);
        expect(note).toMatch(/9 或 13 细浪.*不借未来浪数/);
        expect(note).toMatch(/放量、抵抗、目标与账户门禁/);
    });
    it("documents the independent causal secondary resistance recovery entry", () => {
        const note = topologyProfileNotes.find((text) => text.includes("V3 v103"));
        expect(note).toMatch(/已知二级高点.*当笔或次笔.*空头抵抗/);
        expect(note).toMatch(/放量.*实体严格超过开盘 2%.*收盘.*抵抗高点/);
        expect(note).toMatch(/跳空中大阳.*原起点.*最近未触及/);
        expect(note).toMatch(/旧正 N.*不追溯.*全局风险/);
        const flow = topologyFlows.find((item) => item.id === "secondary-reclaim");
        expect(flow?.gates).toHaveLength(5);
        expect(flow?.gates[0]?.detail).toMatch(/前一交易日.*正式二级高点.*未来/);
        expect(flow?.gates[1]?.detail).toMatch(/固定抵抗高点.*不在这两笔提前买入/);
        expect(flow?.gates[2]?.detail).toMatch(/严格大于开盘 2%.*3%.*60%/);
        expect(flow?.gates[3]?.detail).toMatch(/关闭一般放量过滤也不能免除/);
        expect(flow?.gates[4]?.detail).toMatch(/二吐抵抗.*不跳过近目标.*收盘确认/);
    });
    it("documents independent positive N colors shared by their targets", () => {
        const note = topologyProfileNotes.find((text) => text.includes("独立正 N 按来源 ID"));
        expect(note).toMatch(/分开标识.*不同颜色/);
        expect(note).toMatch(/一饱、二吐、五顶、十满.*目标文字.*对应来源色/);
        expect(note).toMatch(/同价目标不去重、不串色/);
        expect(note).toMatch(/已失效来源不显示但保留其色位.*不读取未来/);
    });
    it("documents dashed touched targets and transparent chart labels", () => {
        const note = topologyProfileNotes.find((text) => text.includes("已触及的 C 目标横线"));
        expect(note).toMatch(/统一用虚线.*长线.*短线样式一致/);
        expect(note).toMatch(/状态、价格和首次触及日期保留/);
        expect(note).toMatch(/目标、正 N 与组合回撤文字均为透明背景/);
        expect(note).toMatch(/趋势方向确认的实线规则保持/);
    });
    it("documents fixed B-date labels without changing C target timing", () => {
        const note = topologyProfileNotes.find((text) => text.includes("C 浪目标标识"));
        expect(note).toMatch(/固定.*B 低点日期上方/);
        expect(note).toMatch(/0\.618.*等浪.*1\.618/);
        expect(note).toMatch(/离开图窗.*隐藏.*移回.*恢复原位置/);
        expect(note).toMatch(/可知日.*首次触及.*保持原规则/);
    });
    it("qualifies the decline-floor launch and independent defense reformations", () => {
        const gate = topologyFlows.flatMap((flow) => flow.gates).find((gate) => gate.id === "bottom-n-target-source");
        expect(gate?.detail).toMatch(/一饱、二吐、五顶、十满.*底部启动资格/);
        expect(gate?.detail).toMatch(/最低点形成的首个正 N.*冻结原箱/);
        expect(gate?.detail).toMatch(/相等低点.*较早底部/);
        expect(gate?.detail).toMatch(/局部 N.*更大结构不重开目标/);
        expect(gate?.detail).toMatch(/按可知日.*原 N 内部.*不追溯/);
        expect(gate?.detail).toMatch(/轧空低.*严格跌破.*失效/);
        expect(gate?.detail).toMatch(/独立重算 N.*不按日期或价格去重/);
        expect(gate?.detail).toMatch(/来源 ID/);
        expect(gate?.yesNext).toBe("direction");
        expect(gate?.noNext).toBe("direction");
    });
    it("requires a complete trend certificate before using structural background", () => {
        const gate = topologyFlows.flatMap((flow) => flow.gates).find((gate) => gate.id === "level-one-wave");
        expect(gate?.detail).toMatch(/一、二、三级.*本级末跌高.*下一级末跌高突破.*空多交替.*收盘.*翻多高点/);
        expect(gate?.detail).toMatch(/仅有下级突破.*不能提前升级.*相等、未知/);
        expect(gate?.detail).toMatch(/一级使用基础折线.*同一证书可知日/);
        expect(gate?.detail).toMatch(/N 候选仍按独立基础折点形成.*继续等待/);
        expect(gate?.yesNext).toBe("pivot");
        expect(gate?.noNext).toBe("pivot");
    });
    it("distinguishes shared-boundary outside C from strict mother-candle geometry", () => {
        const gate = topologyFlows.flatMap((flow) => flow.gates).find((gate) => gate.id === "n-geometry");
        expect(gate?.detail).toMatch(/讲义因果外包 C.*等低创新高.*等高创新低/);
        expect(gate?.detail).toMatch(/两侧完全相等不算外包/);
        expect(gate?.detail).toMatch(/C 在自身收盘确认.*分步转折.*实体.*攻击方向.*B 与 C.*同一收盘/);
        expect(gate?.detail).toMatch(/中间其他棒仍检查/);
        expect(gate?.detail).toMatch(/阳母同日 A\/B.*阴母 A < B = C.*独立严格外包/);
        expect(gate?.detail).toMatch(/等高、等低和十字星不放宽该分支.*默认严格 N 条件保持/);
        expect(gate?.detail).toMatch(/内包阴子.*独立.*A < B = C.*已有确认折线/);
        expect(gate?.detail).toMatch(/母线非十字.*至少一侧严格内缩.*子线自己的颈线.*首次实虚攻击/);
        expect(gate?.detail).toMatch(/底部首个 N 资格.*不以母线旧高替代子线颈线/);
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
        expect(details).toMatch(/一饱.*<.*A 高点.*<.*二吐/);
        expect(details).toMatch(/严格突破一饱即独立确认 A.*不等待 B\/C、二级翻转或轧空/);
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
        expect(new Set(files).size).toBe(files.length);
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
