import { z } from "zod";
const index = z.number().int().nonnegative().max(200_000);
const price = z.number().finite().positive();
const point = z.object({ barIndex: index, ordinal: index }).strict();
const spans = z.array(z.object({ nIndex: index, path: index, originIndex: index, startIndex: index, endIndex: index.nullable() }).strict()).max(200_000);
/** 一只股票共用一份N表；目标只在变化时传快照，各级按区间引用nIndex。 */
export const TrendNDisplaySchema = z.object({
    records: z.array(z.object({
        direction: z.enum(["up", "down"]), points: z.tuple([point, point, point]), knownIndex: index,
        attackIndex: index.nullable(), squeezeIndex: index.nullable(), defense: price.nullable(),
        observedThrough: index.nullable(), invalidatedIndex: index.nullable(),
        targets: z.array(z.object({ knownIndex: index,
            prices: z.tuple([price, price, price.nullable(), price.nullable()]),
            reachedAt: z.tuple([index.nullable(), index.nullable()]),
        }).strict()).max(200_000),
    }).strict()).max(200_000),
    spans: z.tuple([spans, spans, spans, spans]),
}).strict();
type TTrendNDisplay = z.infer<typeof TrendNDisplaySchema>;
/** 拒绝越界、未来回填及同层同路径重叠；不能让显示层猜测归属。 */
export function validateTrendNDisplay(value: TTrendNDisplay, count: number, ctx: z.RefinementCtx): void {
    const issue = (message: string) => ctx.addIssue({ code: "custom", message });
    for (const record of value.records) {
        const clocks = [record.knownIndex, record.attackIndex, record.squeezeIndex, record.observedThrough, record.invalidatedIndex];
        if (clocks.some(clock => clock !== null && clock >= count) || record.points.some(point => point.barIndex > record.knownIndex)) issue("N坐标超过已知行情");
        const [a, b, c] = record.points;
        const before = (left: typeof a, right: typeof a) => left.barIndex < right.barIndex || left.barIndex === right.barIndex && left.ordinal < right.ordinal;
        if (!before(a, b) || !before(b, c)) issue("N点顺序无效");
        let previous = -1;
        for (const target of record.targets) {
            if (target.knownIndex <= previous || target.knownIndex >= count || record.squeezeIndex === null || target.knownIndex < record.squeezeIndex
                || record.observedThrough === null || target.knownIndex > record.observedThrough
                || target.reachedAt.some(clock => clock !== null && clock > target.knownIndex)) issue("N目标快照时钟无效");
            previous = target.knownIndex;
        }
    }
    for (const spans of value.spans) {
        const ends = new Map<number, number>();
        for (const span of spans) {
            const record = value.records[span.nIndex];
            if (!record || span.originIndex > span.startIndex || span.startIndex >= count || span.startIndex < record.knownIndex
                || span.endIndex !== null && (span.endIndex < span.startIndex || span.endIndex >= count)
                || span.startIndex < (ends.get(span.path) ?? -1)) issue("N使用区间或共享索引无效");
            ends.set(span.path, span.endIndex ?? count);
        }
    }
}
