import assert from "node:assert/strict";
import test from "node:test";

import {
    DEFAULT_WATCHLIST_GROUP,
    addWatchlistMembers,
    createWatchlistGroup,
    deleteWatchlistGroup,
    normalizeWatchlistSnapshot,
    removeWatchlistMember,
    renameWatchlistGroup,
} from "../public/watchlists.js";

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

    assert.deepEqual(deleted.groups.map((group) => group.id), ["default"]);
    assert.deepEqual(
        deleted.memberships.map((membership) => membership.symbol).sort(),
        ["sh.600519", "sz.000001"],
    );
    assert.throws(() => deleteWatchlistGroup(deleted, "default"), /不能删除/);
});
