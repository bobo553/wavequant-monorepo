import { num, symbolName } from "./labels.js";

/** @typedef {{run:string,variant:string,scenario:string,source:'tdx'|'snapshot',asof:string,start:string,lookback:number}} ScanParams */
/** @typedef {{id:string,revision:number,params:ScanParams,status:string,total:number,processed:number,failed:number,stale:number,skipped:number,results:Array<object>,errors:Array<object>,error:string|null,performance?:{cache_hits:number,recomputed:number,elapsed_seconds:number}}} ScanJob */
const statusNames = {
    awaiting_next_open: "当日新信号 · 待次开盘验证",
    filled: "历史信号 · 已模拟成交",
    rejected: "历史信号 · 执行被拒绝",
    invalidated: "历史信号 · 后续已失效",
    historical_unfilled: "历史信号 · 未成交",
};
export const profileName = (id) =>
    ({
        lecture_v3: "整段双买点 V3 >1/2 / ≤1/3",
        lecture_v3_d67_c33: "整段双买点 V3 >2/3 / ≤1/3",
        lecture_v3_d50_c50: "整段双买点 V3 >1/2 / ≤1/2",
        lecture_v3_d67_c50: "整段双买点 V3 >2/3 / ≤1/2",
        lecture_v2: "分级双买点 V2",
        lecture_v1: "讲义因果版 V1",
        strict_full: "旧严格折线版",
        proxy_full: "日线代理版",
    })[id] || id;
export function funnelLines(f) {
    if (!f) return [];
    const stocks = {
        no_long_signal: "窗口内无入场信号",
        structure_interrupted: "窗口内结构中断",
        has_n: "窗口内出现 N 完成",
        has_squeeze: "窗口内确认轧空 / 强轧空",
        held_or_not_fresh: "已有持仓或非新买点",
    };
    const rejects = {
        not_squeeze_regime: "不是轧空 / 强轧空",
        bullish_transition_not_ready: "翻多、交替或多头确认未齐备",
        n_attack_not_after_bullish_confirmation: "N 攻击早于多头许可",
        attack_not_in_same_bullish_episode: "攻击不属于当前多头阶段",
        countermove_too_deep: "回档达到 2/3",
        attack_volume_unavailable_or_low: "攻击棒相对量不足或缺失",
        no_live_structural_risk_reward: "无有效止损或未达目标",
        insufficient_close_gross_reward_risk: "收盘盈亏比不足 1.5",
    };
    Object.assign(rejects, {
        wave_no_alternation_at_attack: "N 攻击时尚无已确认空多交替",
        wave_context_no_longer_live: "N 所属趋势阶段已失效或被修订",
        wave_alternation_not_before_n: "交替确认不早于 N 攻击",
        wave_first_pullback_not_deep: "第一类整段回撤未严格超过所选门槛",
        wave_second_peak_pullback_sequence: "第二类再突破、高点、回撤、N 顺序未满足",
        wave_second_close_pullback_too_deep: "第二类整段收盘回撤超过所选上限",
        wave_flip_origin_broken: "价格已跌破翻多最低点 L0",
    });
    return [
        ...Object.entries(f.stocks || {}).map(([k, v]) => `${stocks[k] || k}：${v} 只`),
        ...Object.entries(f.rejections || {})
            .sort((a, b) => b[1] - a[1])
            .map(
                ([k, v]) =>
                    `${rejects[k] || { hierarchy_transition_not_ready: "一级及以上翻多交替未齐备", mature_pullback_sequence_not_ready: "成熟后的新回撤顺序未齐备", mature_pullback_not_shallow: "成熟多头回撤不小于 1/3", early_n_expired_after_maturity: "已成熟，早期 N 的第一类免比例资格失效" }[k] || k}：${v} 次评估`,
            ),
    ];
}
export const scanContextKey = (p) =>
    JSON.stringify([p.run, p.variant, p.scenario, p.source, p.asof, p.start, p.lookback]);
export function sortedMatches(rows) {
    return [...rows].sort(
        (a, b) =>
            b.signal_date.localeCompare(a.signal_date) ||
            (b.priority || 0) - (a.priority || 0) ||
            a.symbol.localeCompare(b.symbol),
    );
}

export class BuyPoints {
    constructor({ api, getContext, onSelect }) {
        Object.assign(this, { api, getContext, onSelect });
        this.$ = (id) => document.getElementById(id);
        this.generation = 0;
        this.$("scan-start").addEventListener("click", () => this.start());
        this.$("scan-cancel").addEventListener("click", () => this.cancel());
        this.$("scan-lookback").addEventListener("change", () => this.contextChanged());
        for (const buy of [false, true])
            this.$(buy ? "buy-points-tab" : "all-stocks-tab").addEventListener("click", () => {
                this.$("stock-list").hidden = buy;
                this.$("stock-search-controls").hidden = buy;
                this.$("buy-points-panel").hidden = !buy;
                this.$("buy-points-tab").setAttribute("aria-pressed", String(buy));
                this.$("all-stocks-tab").setAttribute("aria-pressed", String(!buy));
            });
    }
    params() {
        return { ...this.getContext(), lookback: Number(this.$("scan-lookback").value) };
    }
    contextChanged() {
        if (this.key && this.key !== scanContextKey(this.params())) {
            const old = this.job;
            this.generation++;
            clearTimeout(this.timer);
            this.pollController?.abort();
            this.job = null;
            this.key = null;
            this.$("buy-points-list").replaceChildren();
            this.$("scan-status").textContent = "日期、策略或数据范围已变化，请重新扫描。";
            this.$("scan-start").disabled = false;
            this.$("scan-cancel").disabled = true;
            if (old && ["running", "cancelling"].includes(old.status))
                this.api("/api/buy-scan/cancel", { id: old.id }, undefined, "POST").catch(() => {});
        }
        if (!this.job) this.$("scan-start").disabled = false;
    }
    async start() {
        const generation = ++this.generation;
        clearTimeout(this.timer);
        this.pollController?.abort();
        this.job = null;
        const params = this.params();
        this.key = scanContextKey(params);
        this.$("scan-start").disabled = true;
        this.$("scan-status").textContent = "正在准备股票范围…";
        this.$("buy-points-list").replaceChildren();
        try {
            const job = await this.api("/api/buy-scan", params, undefined, "POST");
            if (generation !== this.generation) {
                if (job.status === "running")
                    this.api("/api/buy-scan/cancel", { id: job.id }, undefined, "POST").catch(() => {});
                return;
            }
            this.job = job;
            this.render();
            this.poll(generation);
        } catch (e) {
            if (generation !== this.generation) return;
            this.$("scan-status").textContent = "扫描启动失败：" + e.message;
            this.$("scan-start").disabled = false;
        }
    }
    async poll(generation) {
        if (generation !== this.generation || !["running", "cancelling"].includes(this.job?.status)) return;
        this.pollController = new AbortController();
        try {
            // Server waits at most 20 seconds, but returns immediately on any result
            // or progress change. No polling interval delays newly matched stocks.
            const job = await this.api(
                "/api/buy-scan",
                { id: this.job.id, after: this.job.revision },
                this.pollController.signal,
            );
            if (generation !== this.generation) return;
            if (job.revision >= this.job.revision) {
                this.job = job;
                this.render();
            }
            this.poll(generation);
        } catch (e) {
            if (generation !== this.generation || e.name === "AbortError") return;
            this.$("scan-status").textContent = "实时更新中断（已显示结果保留，正在重连）：" + e.message;
            this.$("scan-start").disabled = false;
            this.timer = setTimeout(() => this.poll(generation), 1500);
        }
    }
    async cancel() {
        if (!this.job) return;
        const generation = this.generation;
        this.$("scan-cancel").disabled = true;
        try {
            const job = await this.api("/api/buy-scan/cancel", { id: this.job.id }, undefined, "POST");
            if (generation !== this.generation) return;
            if (job.revision >= this.job.revision) {
                this.job = job;
                this.render();
            }
        } catch (e) {
            if (generation === this.generation) {
                this.$("scan-status").textContent = "取消失败：" + e.message;
                this.$("scan-cancel").disabled = false;
            }
        }
    }
    render() {
        const j = this.job,
            p = j.params,
            active = ["running", "cancelling"].includes(j.status);
        this.$("scan-start").disabled = active;
        this.$("scan-cancel").disabled = !active || j.status === "cancelling";
        const stages = {
            running: "扫描中（部分结果）",
            cancelling: "正在取消",
            cancelled: "已取消（部分结果）",
            completed: "扫描完成",
            failed: "扫描失败，结果作废",
        };
        this.$("scan-status").textContent =
            `${p.asof} · ${profileName(p.variant)} · ${p.source === "tdx" ? "通达信主板" : "封存样本"}\n${stages[j.status]} ${j.processed} / ${j.total}，已发现并展示 ${j.results.length} 只（匹配）；跳过 ${j.skipped}，过期 ${j.stale}，失败 ${j.failed}。${active ? "找到即展示，可直接点击复盘。" : ""}${j.current ? `当前计算 ${j.current}。` : ""}${j.error || ""}`;
        if (j.performance && p.source === "tdx")
            this.$("scan-status").textContent +=
                `\n缓存复用 ${j.performance.cache_hits} 只 · 新算 ${j.performance.recomputed} 只 · 用时 ${j.performance.elapsed_seconds.toFixed(1)} 秒（首次及行情更新后需重算）。`;
        const list = this.$("buy-points-list");
        const focused = document.activeElement,
            scrollTop = list.scrollTop;
        const existing = new Map([...list.querySelectorAll(".buy-point-item")].map((b) => [b.dataset.symbol, b]));
        for (const child of [...list.children]) if (!child.classList.contains("buy-point-item")) child.remove();
        const rows = sortedMatches(j.results);
        for (const [index, r] of rows.entries()) {
            let b = existing.get(r.symbol);
            existing.delete(r.symbol);
            if (!b) {
                b = document.createElement("button");
                b.type = "button";
                b.className = "buy-point-item";
                b.dataset.symbol = r.symbol;
                const name = document.createElement("strong");
                name.textContent = r.name ? `${r.symbol.split(".")[1]} ${r.name}` : symbolName(r.symbol);
                const status = document.createElement("span");
                status.textContent = `${r.signal_date} · ${r.buy_point_type ? `${r.priority === 2 ? "第二类 · 重点" : "第一类"} / ${r.trend_level} 级 · ` : ""}${statusNames[r.status]}`;
                const values = document.createElement("small");
                values.textContent = `${r.regime} · 参考 ${num(r.raw_reference_price)} 元（原始） · 相对量 ${num(r.rvol)} · 收盘盈亏比 ${num(r.gross_reward_risk)}`;
                b.append(name, status, values);
                b.addEventListener("click", () => this.onSelect(r, p));
            }
            // Preserve node identity (and clicks) instead of clearing the whole list.
            if (list.children[index] !== b) list.insertBefore(b, list.children[index] || null);
        }
        for (const b of existing.values()) b.remove();
        if (focused?.isConnected && list.contains(focused) && document.activeElement !== focused)
            focused.focus({ preventScroll: true });
        list.scrollTop = scrollTop;
        if (!rows.length) {
            const empty = document.createElement("p");
            empty.className = "stock-empty";
            empty.textContent = active
                ? "正在逐股计算，请勿把暂时为空当成扫描结论。"
                : j.status === "completed"
                  ? "已处理范围内没有匹配信号；失败和跳过的股票不属于已验证范围。"
                  : "未获得完整筛选结果。";
            list.append(empty);
        }
        if (j.funnel) {
            const detail = document.createElement("details"),
                summary = document.createElement("summary"),
                note = document.createElement("p");
            summary.textContent = "查看买点条件诊断";
            note.textContent = "按所选信号窗口统计；各类股票可重叠，评估次数不等于股票数。";
            detail.open = !rows.length;
            detail.append(summary, note);
            for (const text of funnelLines(j.funnel)) {
                const line = document.createElement("p");
                line.textContent = text;
                detail.append(line);
            }
            list.append(detail);
        }
        if (j.errors.length || j.skipped) {
            const detail = document.createElement("details"),
                summary = document.createElement("summary");
            summary.textContent = "查看跳过 / 失败原因";
            detail.append(summary);
            const names = {
                unsupported_board: "执行模型不支持的板块",
                no_daily: "无有效日线",
                current_st_or_delisted: "当前 ST / 退市",
                stale_daily: "行情未更新到回放日",
            };
            for (const [reason, n] of Object.entries(j.skip_reasons || {})) {
                const line = document.createElement("p");
                line.textContent = `${names[reason] || reason}：${n}`;
                detail.append(line);
            }
            for (const e of j.errors) {
                const line = document.createElement("p");
                line.textContent = `${e.symbol}：${e.error}`;
                detail.append(line);
            }
            list.append(detail);
        }
    }
}
