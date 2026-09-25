import { isRecoverableBacktestError } from "./backtest-retry.js";

const DEFAULT_VERSION_REFRESH_MS = 30_000;
const FAILURE_RETRY_DELAYS_MS = [5_000, 15_000, 45_000];

export function backtestArgumentsKey(path, params) {
    return JSON.stringify([
        path,
        Object.entries(params)
            .filter(([key]) => key !== "backtest_job")
            .sort(([left], [right]) => left.localeCompare(right))
            .map(([key, value]) => [key, String(value)]),
    ]);
}

export function watchlistBacktestRequest(member, context) {
    const path = `/api/${context.source}-backtest`;
    const params = {
        run: context.run,
        variant: context.variant,
        scenario: context.scenario,
        start: context.start,
        volume_filter: context.volume_filter,
        net_reward_risk_filter: context.net_reward_risk_filter,
        shallow_base_breakout_enabled: context.shallow_base_breakout_enabled,
        initial_capital: context.initial_capital,
        max_position_weight: context.max_position_weight,
        symbol: member.symbol,
        asof: member.asof,
    };
    return { path, params };
}

/** Runs the visible watchlist in order while the research workbench is idle. */
export class IdleWatchlistBacktests {
    constructor({ snapshot, version, run, isIdle, onChange, onCompleted, onRejected, serverJobs = () => [], hasCapacity = () => true, now = Date.now, versionRefreshMs = DEFAULT_VERSION_REFRESH_MS }) {
        Object.assign(this, { snapshot, version, run, isIdle, onChange, onCompleted, onRejected, serverJobs, hasCapacity, now, versionRefreshMs });
        this.enabled = true;
        this.contextKey = null;
        this.membershipKey = null;
        this.strategyVersion = null;
        this.lastVersionCheck = -Infinity;
        this.generation = 0;
        this.statuses = new Map();
        this.completedJobs = new Map();
        this.fillCounts = new Map();
        this.failures = new Map();
        this.retryAt = new Map();
        this.retryCounts = new Map();
        this.members = [];
        this.active = null;
        this.checking = false;
        this.error = "";
        this.blockedUntil = 0;
        this.lastEmitted = "";
    }

    start(intervalMs = 1_000) {
        if (this.timer) return;
        this.timer = setInterval(() => void this.tick(), intervalMs);
        void this.tick();
    }

    stop() {
        clearInterval(this.timer);
        this.timer = null;
    }

    setEnabled(enabled) {
        this.enabled = Boolean(enabled);
        this.emit();
        if (this.enabled) void this.tick();
    }

    sync(snapshot) {
        const members = snapshot?.members || [];
        const { group, ...calculationContext } = snapshot?.context || {};
        const contextKey = snapshot ? JSON.stringify(calculationContext) : null;
        const membershipKey = snapshot ? JSON.stringify([group, members.map(({ symbol, asof }) => [symbol, asof])]) : null;
        if (contextKey === this.contextKey && membershipKey === this.membershipKey) return;
        const contextChanged = contextKey !== this.contextKey;
        const previousAsOf = new Map(this.members.map((member) => [member.symbol, member.asof]));
        this.contextKey = contextKey;
        this.membershipKey = membershipKey;
        this.context = snapshot?.context || null;
        this.members = members;
        if (contextChanged) {
            this.active?.controller.abort();
            this.strategyVersion = null;
            this.lastVersionCheck = -Infinity;
            this.statuses.clear();
            this.completedJobs.clear();
            this.fillCounts.clear();
            this.failures.clear();
            this.retryAt.clear();
            this.retryCounts.clear();
            this.generation++;
            this.error = "";
        } else {
            const currentAsOf = new Map(members.map((member) => [member.symbol, member.asof]));
            if (this.active && currentAsOf.get(this.active.member.symbol) !== this.active.member.asof)
                this.active.controller.abort();
            for (const symbol of this.statuses.keys()) {
                if (!currentAsOf.has(symbol) || previousAsOf.get(symbol) !== currentAsOf.get(symbol)) {
                    this.statuses.delete(symbol);
                    this.completedJobs.delete(symbol);
                    this.fillCounts.delete(symbol);
                    this.failures.delete(symbol);
                    this.retryAt.delete(symbol);
                    this.retryCounts.delete(symbol);
                }
            }
        }
        this.emit();
    }

    state() {
        const completed = [...this.statuses.values()].filter((status) => status === "completed").length;
        const failed = [...this.statuses.values()].filter((status) => status === "failed").length;
        const active = this.active && this.active.generation === this.generation &&
            this.members.some((member) => member.symbol === this.active.member.symbol && member.asof === this.active.member.asof)
            ? this.active.member : null;
        return {
            enabled: this.enabled,
            ready: Boolean(this.contextKey),
            version: this.strategyVersion,
            source: this.context?.source || null,
            checking: this.checking,
            idle: this.isIdle(),
            active,
            queued: Boolean(active?.queued),
            draining: Boolean(this.active && !active),
            statuses: Object.fromEntries(this.statuses),
            fillCounts: Object.fromEntries(this.fillCounts),
            failures: Object.fromEntries(this.failures),
            total: this.members.length,
            completed,
            failed,
            retrying: this.retryAt.size,
            capacityFull: !this.hasCapacity(),
            error: this.error,
        };
    }

    emit() {
        const state = this.state();
        const serialized = JSON.stringify(state);
        if (serialized === this.lastEmitted) return;
        this.lastEmitted = serialized;
        this.onChange(state);
    }

    matchingJobId(path, params) {
        const job = this.active?.job;
        if (job?.path === path && backtestArgumentsKey(path, job.params) === backtestArgumentsKey(path, params))
            return job.params.backtest_job;
        const completed = this.completedJobs.get(params.symbol);
        return completed?.path === path && backtestArgumentsKey(path, completed.params) === backtestArgumentsKey(path, params)
            ? completed.params.backtest_job : null;
    }

    hasCompleted(symbol, source) {
        return this.context?.source === source && this.statuses.get(symbol) === "completed" && this.completedJobs.has(symbol);
    }

    adoptCompleted(path, params, result, version) {
        const member = this.members.find((item) => item.symbol === params.symbol && item.asof === params.asof);
        if (!member || !this.context || !this.strategyVersion || version !== this.strategyVersion ||
            result?.symbol !== member.symbol || result?.asof !== member.asof ||
            result?.result_scope !== "stock" || !result?.backtest || result.backtest.status === "data_unavailable") return false;
        const expected = watchlistBacktestRequest(member, this.context);
        if (path !== expected.path || backtestArgumentsKey(path, params) !== backtestArgumentsKey(expected.path, expected.params))
            return false;
        if (this.statuses.get(member.symbol) === "completed") return true;
        this.statuses.set(member.symbol, "completed");
        this.completedJobs.set(member.symbol, { path, params });
        this.fillCounts.set(member.symbol, (result.orders || []).filter((order) => order.status === "filled").length);
        this.failures.delete(member.symbol);
        this.retryAt.delete(member.symbol);
        this.retryCounts.delete(member.symbol);
        this.emit();
        return true;
    }

    adoptServerStatus(record) {
        const member = this.members.find((item) => item.symbol === record?.symbol && item.asof === record?.params?.asof);
        if (!member || !this.context || !this.strategyVersion || record.version !== this.strategyVersion) return false;
        const expected = watchlistBacktestRequest(member, this.context);
        if (record.path !== expected.path ||
            backtestArgumentsKey(record.path, record.params) !== backtestArgumentsKey(expected.path, expected.params)) return false;
        if (this.active?.member.symbol === member.symbol && this.active.job?.params.backtest_job !== record.job) return false;
        if (record.status === "completed" && record.result_valid === true) {
            if (this.statuses.get(member.symbol) === "completed") return false;
            this.statuses.set(member.symbol, "completed");
            if (record.result_available && record.job) {
                this.completedJobs.set(member.symbol, {
                    path: record.path,
                    params: { ...record.params, backtest_job: record.job },
                });
            } else this.completedJobs.delete(member.symbol);
            if (Number.isInteger(record.fill_count)) this.fillCounts.set(member.symbol, record.fill_count);
            this.failures.delete(member.symbol);
            this.retryAt.delete(member.symbol);
            this.retryCounts.delete(member.symbol);
            this.emit();
            return true;
        }
        if (record.status === "failed" && this.statuses.get(member.symbol) !== "completed" &&
            this.statuses.get(member.symbol) !== "failed") {
            this.statuses.set(member.symbol, "failed");
            this.failures.set(member.symbol, "服务器回测失败，可点击重试");
            this.emit();
            return true;
        }
        return false;
    }

    retryFailed() {
        for (const [symbol, status] of this.statuses) {
            if (status === "failed") this.statuses.delete(symbol);
        }
        this.failures.clear();
        this.retryAt.clear();
        this.retryCounts.clear();
        this.emit();
        void this.tick();
    }

    async ensureVersion(snapshot = this.snapshot()) {
        this.sync(snapshot);
        if (!snapshot || !this.members.length) return false;
        if (this.checking) {
            await this.versionRequest.catch(() => {});
            return Boolean(this.strategyVersion);
        }
        if (this.now() - this.lastVersionCheck < this.versionRefreshMs) return Boolean(this.strategyVersion);
        const generation = this.generation;
        this.checking = true;
        this.emit();
        try {
            this.versionRequest = this.version(snapshot);
            const response = await this.versionRequest;
            if (generation !== this.generation) return false;
            if (typeof response?.version !== "string" || !response.version) throw new Error("策略版本响应无效");
            if (this.strategyVersion !== response.version) {
                this.strategyVersion = response.version;
                this.statuses.clear();
                this.completedJobs.clear();
                this.fillCounts.clear();
                this.failures.clear();
                this.retryAt.clear();
                this.retryCounts.clear();
                this.generation++;
            }
            this.error = "";
            this.lastVersionCheck = this.now();
            return true;
        } catch (error) {
            if (generation === this.generation) {
                this.strategyVersion = null;
                this.error = error.message || "策略版本读取失败";
                this.lastVersionCheck = this.now();
            }
            return false;
        } finally {
            this.checking = false;
            this.versionRequest = null;
            this.emit();
        }
    }

    async tick() {
        const snapshot = this.snapshot();
        this.sync(snapshot);
        if (!snapshot || !this.members.length || this.now() < this.blockedUntil) {
            this.emit();
            return;
        }
        if (this.checking || this.active) return;
        if (!await this.ensureVersion(snapshot)) return;
        if (!this.enabled || !this.isIdle() || !this.strategyVersion || !this.hasCapacity()) return;
        const busySymbols = new Set(this.serverJobs().map((job) => job.symbol || job.params?.symbol));
        const member = this.members.find(({ symbol }) => !busySymbols.has(symbol) && !this.statuses.has(symbol)) ||
            this.members.find(({ symbol }) => !busySymbols.has(symbol) && this.statuses.get(symbol) === "failed" && this.retryAt.has(symbol) && this.retryAt.get(symbol) <= this.now());
        if (!member) {
            this.emit();
            return;
        }
        const generation = this.generation;
        this.active = { member, generation, controller: new AbortController() };
        const active = this.active;
        this.retryAt.delete(member.symbol);
        this.statuses.set(member.symbol, "running");
        this.emit();
        try {
            const result = await this.run(member, snapshot, active);
            if (result?.backtest?.status === "data_unavailable") throw new Error(result.evidence || "当前数据源无法完成回测");
            if (!result?.backtest || result.symbol !== member.symbol || result.result_scope !== "stock")
                throw new Error("回测结果与请求股票不一致");
            if (generation === this.generation && this.members.some((item) => item.symbol === member.symbol && item.asof === member.asof)) {
                this.statuses.set(member.symbol, "completed");
                if (active.job) this.completedJobs.set(member.symbol, active.job);
                this.fillCounts.set(member.symbol, (result.orders || []).filter((order) => order.status === "filled").length);
                this.failures.delete(member.symbol);
                this.retryAt.delete(member.symbol);
                this.retryCounts.delete(member.symbol);
                this.onCompleted?.(result, active);
            }
        } catch (error) {
            if (generation === this.generation && this.members.some((item) => item.symbol === member.symbol && item.asof === member.asof)) {
                if (["BACKTEST_CAPACITY", "BACKTEST_SYMBOL_RUNNING"].includes(error?.code)) {
                    this.statuses.delete(member.symbol);
                    this.failures.delete(member.symbol);
                    this.retryAt.delete(member.symbol);
                    this.blockedUntil = this.now() + 5_000;
                    this.onRejected?.(error);
                } else {
                    this.statuses.set(member.symbol, "failed");
                    this.failures.set(member.symbol, error.message || "回测失败");
                    const attempt = (this.retryCounts.get(member.symbol) || 0) + 1;
                    this.retryCounts.set(member.symbol, attempt);
                    if (isRecoverableBacktestError(error) && attempt <= FAILURE_RETRY_DELAYS_MS.length)
                        this.retryAt.set(member.symbol, this.now() + FAILURE_RETRY_DELAYS_MS[attempt - 1]);
                    else this.retryAt.delete(member.symbol);
                }
            }
        } finally {
            if (this.active === active) this.active = null;
            this.emit();
        }
    }
}
