import { num, pct } from "./labels.js";

export const ratioPlans = [
    ["lecture_v3", "二/三级交替后 N 轧空 / ≤1/3"],
    ["lecture_v3_d67_c33", ">2/3 / ≤1/3"],
    ["lecture_v3_d50_c50", ">1/2 / ≤1/2"],
    ["lecture_v3_d67_c50", ">2/3 / ≤1/2"],
    ["lecture_v3_close_d50_c50", "收盘 >1/2 / 收盘 <1/2"],
];
/** @typedef {{run:string,symbol:string,asof:string,start:string,scenario:string,volume_filter:boolean,net_reward_risk_filter:boolean,initial_capital?:number,max_position_weight?:number,local:boolean,source?:string}} RatioContext */
export const ratioContextKey = (p) =>
    JSON.stringify([
        p.run,
        p.symbol,
        p.asof,
        p.start,
        p.scenario,
        p.volume_filter,
        p.net_reward_risk_filter ?? false,
        p.initial_capital,
        p.max_position_weight,
        p.local,
        p.source ?? "tdx",
    ]);
export function comparisonValues(view) {
    const m = view.metrics;
    return [
        num(view.backtest.counts.long_signals || 0, 0),
        num(m.entry_fills, 0),
        num(m.trades, 0),
        m.trades && m.win_rate != null ? pct(m.win_rate) : "无平仓样本",
        pct(m.total_return),
        pct(m.max_drawdown),
    ];
}
export class RatioComparison {
    constructor({ api, getContext, onSelect }) {
        Object.assign(this, { api, getContext, onSelect });
        this.generation = 0;
        this.$ = (id) => document.getElementById(id);
        this.$("compare-ratios").addEventListener("click", () => this.run());
        this.$("cancel-ratios").addEventListener("click", () => this.cancel());
    }
    contextChanged() {
        const p = this.getContext(),
            key = ratioContextKey(p);
        if (this.key && this.key !== key) {
            this.cancel();
            this.$("ratio-results").replaceChildren();
            this.$("ratio-status").textContent = "股票、日期或成本已变化，请重新对比。";
        }
        this.$("compare-ratios").disabled = !p.local || this.running;
    }
    cancel() {
        this.generation++;
        this.controller?.abort();
        this.running = false;
        this.$("cancel-ratios").disabled = true;
        this.$("compare-ratios").disabled = !this.getContext().local;
        this.$("ratio-status").textContent = "已取消，表中仅为已完成的部分方案。";
    }
    async run() {
        const p = this.getContext();
        if (!p.local) return;
        const generation = ++this.generation;
        this.controller?.abort();
        this.controller = new AbortController();
        this.running = true;
        this.key = ratioContextKey(p);
        this.$("ratio-results").replaceChildren();
        this.$("compare-ratios").disabled = true;
        this.$("cancel-ratios").disabled = false;
        let completed = 0,
            failed = 0;
        for (const [variant, title] of ratioPlans) {
            if (generation !== this.generation) return;
            this.$("ratio-status").textContent =
                `${p.symbol} · ${p.start} — ${p.asof} · ${p.scenario}：已完成 ${completed}/${ratioPlans.length}，正在计算 ${title}…`;
            const tr = document.createElement("tr");
            try {
                const { local, source = "tdx", volume_filter, net_reward_risk_filter = false, ...params } = p;
                const view = await this.api(
                    source === "akshare" ? "/api/akshare-backtest" : "/api/tdx-backtest",
                    {
                        ...params,
                        volume_filter: String(volume_filter),
                        net_reward_risk_filter: String(net_reward_risk_filter),
                        variant,
                    },
                    this.controller.signal,
                );
                if (generation !== this.generation) return;
                if (view.backtest?.status === "data_unavailable") throw new Error(view.evidence);
                for (const value of [title, ...comparisonValues(view)]) {
                    const td = document.createElement("td");
                    td.textContent = value;
                    tr.append(td);
                }
                const td = document.createElement("td"),
                    button = document.createElement("button");
                button.textContent = "查看 B/S";
                button.addEventListener("click", () => this.onSelect(variant));
                td.append(button);
                tr.append(td);
            } catch (e) {
                if (generation !== this.generation) return;
                failed++;
                const td = document.createElement("td");
                td.colSpan = 8;
                td.textContent = `${title}：计算失败，${e.message}`;
                tr.append(td);
            }
            this.$("ratio-results").append(tr);
            completed++;
        }
        this.running = false;
        this.$("compare-ratios").disabled = false;
        this.$("cancel-ratios").disabled = true;
        this.$("ratio-status").textContent =
            `${p.symbol} · ${p.start} — ${p.asof} · ${p.scenario}：${ratioPlans.length} 组对比结束，失败 ${failed} 组。胜率仅统计已平仓净盈利交易；未平仓不计入胜率。`;
    }
}
