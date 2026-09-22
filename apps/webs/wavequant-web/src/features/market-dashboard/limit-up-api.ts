import { readApi } from "@/shared/api/api-client";
import { LimitUpLadderSchema, type TLimitUpLadder } from "@repo/contracts";

export async function loadLimitUpLadder(date: string, refresh: boolean, signal: AbortSignal): Promise<TLimitUpLadder> {
    const query = new URLSearchParams({ refresh: String(refresh) });
    if (date) query.set("date", date);
    const result = LimitUpLadderSchema.parse(await readApi(`/api/limit-up-ladder?${query}`, signal));
    if (date && result.date !== date) throw new Error("返回日期与所选日期不一致");
    return result;
}

export function chinaToday(): string {
    return new Intl.DateTimeFormat("en-CA", {
        timeZone: "Asia/Shanghai",
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
    }).format(new Date());
}

export function shiftCalendarDate(date: string, days: number): string {
    const value = new Date(`${date}T12:00:00Z`);
    value.setUTCDate(value.getUTCDate() + days);
    return value.toISOString().slice(0, 10);
}
