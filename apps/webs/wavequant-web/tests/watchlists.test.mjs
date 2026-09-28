import assert from "node:assert/strict";
import test from "node:test";

import { JSDOM } from "jsdom";

import {
    DEFAULT_WATCHLIST_GROUP,
    Watchlists,
    addWatchlistMembers,
    createWatchlistGroup,
    deleteWatchlistGroup,
    firstAvailableWatchlistSymbol,
    normalizeWatchlistSnapshot,
    removeWatchlistMember,
    renameWatchlistGroup,
    reorderWatchlistMembers,
} from "../public/watchlists.js";

test("dragging from the stock button reorders the row without activating its click", async () => {
    const dom = new JSDOM(`
        <div id="list">
            <div class="watchlist-stock-row" data-symbol="sh.600001">
                <button class="watchlist-stock-drag"></button><button class="watchlist-stock-open"></button>
            </div>
            <div class="watchlist-stock-row" data-symbol="sh.600002"></div>
        </div>
    `);
    const previousWindow = globalThis.window;
    const previousDocument = globalThis.document;
    globalThis.window = dom.window;
    globalThis.document = dom.window.document;
    try {
        const list = document.getElementById("list");
        const row = list.firstElementChild;
        const handle = row.querySelector(".watchlist-stock-drag");
        const open = row.querySelector(".watchlist-stock-open");
        window.matchMedia = () => ({ matches: true });
        list.getBoundingClientRect = () => ({ top: 0, bottom: 80 });
        for (const item of list.children) {
            item.getBoundingClientRect = () => ({
                left: 0,
                top: [...list.children].indexOf(item) * 40,
                width: 180,
                height: 40,
            });
            Object.defineProperty(item, "offsetHeight", { value: 40 });
            item.getAnimations = () => [];
        }
        const saved = [];
        const subject = {
            reordering: false,
            orderAnimation: Promise.resolve(),
            animateOrder(_list, rearrange) {
                rearrange();
                this.orderAnimation = Promise.resolve();
            },
            saveOrder(symbols) {
                saved.push(symbols);
                return Promise.resolve();
            },
        };
        Watchlists.prototype.bindOrderHandle.call(subject, handle, row, list);
        let clicks = 0;
        open.addEventListener("click", () => clicks++);
        const pointer = (target, type, clientY) => {
            const event = new window.Event(type, { bubbles: true, cancelable: true });
            Object.assign(event, { button: 0, isPrimary: true, pointerId: 1, clientY });
            target.dispatchEvent(event);
        };

        pointer(open, "pointerdown", 10);
        pointer(window, "pointerup", 10);
        open.click();
        assert.equal(clicks, 1);

        pointer(open, "pointerdown", 10);
        pointer(window, "pointermove", 90);
        pointer(window, "pointerup", 90);
        open.click();
        await subject.orderAnimation;
        assert.equal(clicks, 1);
        assert.deepEqual(
            [...list.children].map((item) => item.dataset.symbol),
            ["sh.600002", "sh.600001"],
        );
        assert.deepEqual(saved, [["sh.600002", "sh.600001"]]);
        assert.equal(document.querySelector(".watchlist-stock-ghost"), null);

        const touch = (target, type, clientY) => {
            const event = new window.Event(type, { bubbles: true, cancelable: true });
            const point = { identifier: 1, clientY };
            Object.assign(event, {
                touches: type === "touchend" ? [] : [point],
                changedTouches: [point],
            });
            target.dispatchEvent(event);
            return event;
        };
        touch(open, "touchstart", 70);
        const scroll = touch(window, "touchmove", 10);
        touch(window, "touchend", 10);
        assert.equal(scroll.defaultPrevented, false);
        assert.deepEqual(saved, [["sh.600002", "sh.600001"]]);

        touch(open, "touchstart", 70);
        await new Promise((resolve) => window.setTimeout(resolve, 320));
        const dragMove = touch(window, "touchmove", 10);
        touch(window, "touchend", 10);
        open.click();
        await subject.orderAnimation;
        assert.equal(dragMove.defaultPrevented, true);
        assert.equal(clicks, 1);
        assert.deepEqual(
            [...list.children].map((item) => item.dataset.symbol),
            ["sh.600001", "sh.600002"],
        );
        assert.deepEqual(saved[1], ["sh.600001", "sh.600002"]);
    } finally {
        dom.window.close();
        if (previousWindow === undefined) delete globalThis.window;
        else globalThis.window = previousWindow;
        if (previousDocument === undefined) delete globalThis.document;
        else globalThis.document = previousDocument;
    }
});

test("a running watchlist row shows its own server progress and clears it after completion", () => {
    const badge = { dataset: {}, textContent: "", title: "" };
    const fillCount = { hidden: true, textContent: "" };
    const result = { hidden: true, dataset: {}, textContent: "", title: "" };
    const drawdown = { hidden: true, textContent: "" };
    const secondary = { hidden: true };
    const open = {
        dataset: { baseLabel: "切换到 瑞凌股份" },
        setAttribute(name, value) {
            this[name] = value;
        },
    };
    const row = {
        querySelector(selector) {
            return {
                ".watchlist-backtest-badge": badge,
                ".watchlist-backtest-fill-count": fillCount,
                ".watchlist-backtest-result": result,
                ".watchlist-backtest-drawdown": drawdown,
                ".watchlist-stock-secondary": secondary,
                ".watchlist-stock-open": open,
            }[selector];
        },
    };
    const watchlists = {
        backtestStatuses: { "sz.300154": "running" },
        backtestEligible: null,
        backtestJobs: [{ status: "running", symbol: "sz.300154", progress_percent: 46, progress_stage: "模拟成交" }],
        backtestFillCounts: {},
        backtestReturns: {},
        backtestFailures: {},
    };
    Watchlists.prototype.renderBacktestStatus.call(watchlists, row, "sz.300154");
    assert.equal(badge.textContent, "回测中 · 46%");
    assert.equal(badge.title, "回测中 · 46% · 模拟成交");
    assert.match(open["aria-label"], /回测中 · 46%/);

    watchlists.backtestStatuses["sz.300154"] = "completed";
    watchlists.backtestJobs = [];
    watchlists.backtestFillCounts["sz.300154"] = 2;
    watchlists.backtestReturns["sz.300154"] = { rate: 0.125, amount: 12500, drawdown: -0.083 };
    Watchlists.prototype.renderBacktestStatus.call(watchlists, row, "sz.300154");
    assert.equal(badge.textContent, "已完成");
    assert.equal(fillCount.textContent, "2笔成交");
    assert.equal(fillCount.hidden, false);
    assert.equal(result.textContent, "盈 +12.50%");
    assert.equal(drawdown.textContent, "最大回撤 -8.30%");
    assert.equal(secondary.hidden, false);
    assert.match(open["aria-label"], /最大回撤 -8\.30%/);

    watchlists.backtestStatuses["sz.300154"] = "historical";
    Watchlists.prototype.renderBacktestStatus.call(watchlists, row, "sz.300154");
    assert.equal(fillCount.hidden, true);
    assert.equal(result.hidden, true);
    assert.equal(drawdown.hidden, true);
    assert.equal(secondary.hidden, true);
});

test("normalization repairs malformed watchlist data without mutating the stored snapshot", () => {
    const snapshot = {
        groups: [
            { id: "later", name: "  长线  ", position: 2 },
            { id: "earlier", name: "短线", position: 1 },
            { id: "duplicate-name", name: "短线", position: 3 },
        ],
        memberships: [
            { groupId: "earlier", symbol: "sh.600519", name: "贵州茅台" },
            { groupId: "earlier", symbol: "sh.600519", name: "重复" },
            { groupId: "missing", symbol: "sz.000001", name: "无效分组" },
        ],
    };
    const originalGroups = structuredClone(snapshot.groups);

    const normalized = normalizeWatchlistSnapshot(snapshot);

    assert.deepEqual(snapshot.groups, originalGroups);
    assert.deepEqual(
        normalized.groups.map(({ id, name, protected: isProtected }) => ({ id, name, protected: isProtected })),
        [
            { id: DEFAULT_WATCHLIST_GROUP.id, name: DEFAULT_WATCHLIST_GROUP.name, protected: true },
            { id: "earlier", name: "短线", protected: false },
            { id: "later", name: "长线", protected: false },
        ],
    );
    assert.equal(normalized.memberships.length, 1);
});

test("initial symbol follows the first available row in the selected watchlist category", () => {
    const group = createWatchlistGroup(null, "重点跟踪", "focus");
    const withMembers = addWatchlistMembers(group.state, "focus", [
        { symbol: "sh.600519", name: "重庆样本" },
        { symbol: "sz.000001", name: "阿尔法样本" },
        { symbol: "sh.600000", name: "北京样本" },
    ]).state;
    const universe = [
        { symbol: "sh.600519", has_data: true },
        { symbol: "sz.000001", has_data: false },
        { symbol: "sh.600000", has_data: true },
    ];

    assert.equal(firstAvailableWatchlistSymbol(withMembers, "focus", universe), "sh.600000");
    assert.equal(firstAvailableWatchlistSymbol(withMembers, "default", universe), "");
    assert.equal(firstAvailableWatchlistSymbol(withMembers, "focus", []), "");
});

test("reordering a category survives storage order and leaves other categories unchanged", () => {
    const custom = createWatchlistGroup(null, "重点跟踪", "focus");
    const defaults = addWatchlistMembers(custom.state, "default", [{ symbol: "bj.430001", name: "默认样本" }]);
    const added = addWatchlistMembers(defaults.state, "focus", [
        { symbol: "sh.600519", name: "重庆样本" },
        { symbol: "sz.000001", name: "阿尔法样本" },
        { symbol: "sh.600000", name: "北京样本" },
    ]);
    const desiredOrder = ["sh.600000", "sh.600519", "sz.000001"];
    const reordered = reorderWatchlistMembers(added.state, "focus", desiredOrder);
    const reloaded = normalizeWatchlistSnapshot({
        ...reordered,
        memberships: [...reordered.memberships].reverse(),
    });

    assert.deepEqual(
        reloaded.memberships.filter((member) => member.groupId === "focus").map((member) => member.symbol),
        desiredOrder,
    );
    assert.equal(
        firstAvailableWatchlistSymbol(reloaded, "focus", [{ symbol: "sh.600000", has_data: true }]),
        "sh.600000",
    );
    assert.deepEqual(
        reloaded.memberships.filter((member) => member.groupId === "default").map((member) => member.symbol),
        ["bj.430001"],
    );
    const appended = addWatchlistMembers(reloaded, "focus", [{ symbol: "sz.000002", name: "最前样本" }]).state;
    assert.deepEqual(
        appended.memberships.filter((member) => member.groupId === "focus").map((member) => member.symbol),
        [...desiredOrder, "sz.000002"],
    );
    assert.throws(() => reorderWatchlistMembers(reloaded, "focus", ["sh.600000"]), /顺序已变化/);
});

test("category names are unique and the protected default category cannot be renamed", () => {
    const created = createWatchlistGroup(null, "  重点跟踪  ", "focus");
    assert.equal(created.group.name, "重点跟踪");
    assert.throws(() => createWatchlistGroup(created.state, "重点跟踪", "other"), /已存在/);
    assert.throws(() => renameWatchlistGroup(created.state, "default", "新名称"), /不能重命名/);
    assert.equal(renameWatchlistGroup(created.state, "focus", "短线").groups[1].name, "短线");
});

test("adding stocks is idempotent and removing a member only affects its category", () => {
    const created = createWatchlistGroup(null, "短线", "short");
    const first = addWatchlistMembers(created.state, "short", [
        { symbol: "sh.600519", name: "贵州茅台" },
        { symbol: "sh.600519", name: "重复输入" },
        { symbol: "invalid", name: "无效代码" },
    ]);
    assert.equal(first.added, 1);
    assert.equal(first.duplicates, 2);

    const second = addWatchlistMembers(first.state, "short", [{ symbol: "sh.600519", name: "贵州茅台" }]);
    assert.equal(second.added, 0);
    assert.equal(second.state.memberships.length, 1);
    assert.equal(removeWatchlistMember(second.state, "default", "sh.600519").memberships.length, 1);
    assert.equal(removeWatchlistMember(second.state, "short", "sh.600519").memberships.length, 0);
});

test("deleting a category migrates unique members into the protected default category", () => {
    const created = createWatchlistGroup(null, "事件驱动", "events");
    const defaults = addWatchlistMembers(created.state, "default", [{ symbol: "sh.600519", name: "贵州茅台" }]);
    const custom = addWatchlistMembers(defaults.state, "events", [
        { symbol: "sh.600519", name: "贵州茅台" },
        { symbol: "sz.000001", name: "平安银行" },
    ]);

    const deleted = deleteWatchlistGroup(custom.state, "events");

    assert.deepEqual(
        deleted.groups.map((group) => group.id),
        ["default"],
    );
    assert.deepEqual(deleted.memberships.map((membership) => membership.symbol).sort(), ["sh.600519", "sz.000001"]);
    assert.throws(() => deleteWatchlistGroup(deleted, "default"), /不能删除/);
});
