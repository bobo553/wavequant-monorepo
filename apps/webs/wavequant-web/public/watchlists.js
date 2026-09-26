const DATABASE_NAME = "wavequant-user-data";
const DATABASE_VERSION = 1;
const GROUP_STORE = "watchlist-groups";
const MEMBERSHIP_STORE = "watchlist-memberships";
const SELECTED_GROUP_KEY = "wavequant.watchlists.selected.v1";
const COLLAPSED_RAIL_KEY = "wavequant.watchlists.rail-collapsed.v1";
const MAX_GROUPS = 20;
const MAX_MEMBERS_PER_GROUP = 1000;
const SYMBOL_PATTERN = /^(?:sh|sz|bj)\.\d{6}$/;

export const DEFAULT_WATCHLIST_GROUP = Object.freeze({
    id: "default",
    name: "我的自选",
    position: 0,
    protected: true,
});

export function setWatchlistStarIcon(button, filled) {
    const source = document.getElementById(`watchlist-star-${filled ? "filled" : "outline"}-icon`);
    const icon = source?.cloneNode(true);
    if (icon) {
        icon.removeAttribute("id");
        icon.setAttribute("aria-hidden", "true");
        button.replaceChildren(icon);
    }
}

function validGroup(group) {
    return (
        group &&
        typeof group.id === "string" &&
        group.id.length <= 64 &&
        typeof group.name === "string" &&
        group.name.trim().length > 0 &&
        group.name.trim().length <= 24
    );
}

function validMembership(membership, groupIds) {
    return (
        membership &&
        groupIds.has(membership.groupId) &&
        typeof membership.symbol === "string" &&
        SYMBOL_PATTERN.test(membership.symbol)
    );
}

export function normalizeWatchlistSnapshot(snapshot) {
    const sourceGroups = Array.isArray(snapshot?.groups) ? snapshot.groups : [];
    const groupIds = new Set([DEFAULT_WATCHLIST_GROUP.id]);
    const names = new Set([DEFAULT_WATCHLIST_GROUP.name.toLocaleLowerCase("zh-CN")]);
    const groups = [{ ...DEFAULT_WATCHLIST_GROUP }];
    for (const group of [...sourceGroups].sort((left, right) => Number(left?.position) - Number(right?.position))) {
        if (!validGroup(group) || group.id === DEFAULT_WATCHLIST_GROUP.id || groupIds.has(group.id)) continue;
        const name = group.name.trim();
        const nameKey = name.toLocaleLowerCase("zh-CN");
        if (names.has(nameKey) || groups.length >= MAX_GROUPS) continue;
        groupIds.add(group.id);
        names.add(nameKey);
        groups.push({
            id: group.id,
            name,
            position: groups.length,
            protected: false,
            createdAt: typeof group.createdAt === "string" ? group.createdAt : undefined,
            updatedAt: typeof group.updatedAt === "string" ? group.updatedAt : undefined,
        });
    }
    const memberships = [];
    const membershipKeys = new Set();
    for (const membership of Array.isArray(snapshot?.memberships) ? snapshot.memberships : []) {
        if (!validMembership(membership, groupIds)) continue;
        const key = `${membership.groupId}:${membership.symbol}`;
        if (membershipKeys.has(key)) continue;
        membershipKeys.add(key);
        memberships.push({
            groupId: membership.groupId,
            symbol: membership.symbol,
            name: typeof membership.name === "string" ? membership.name.slice(0, 80) : "",
            createdAt: typeof membership.createdAt === "string" ? membership.createdAt : undefined,
            position:
                Number.isSafeInteger(membership.position) && membership.position >= 0 ? membership.position : null,
        });
    }
    const orderedMemberships = groups.flatMap((group) =>
        memberships
            .filter((membership) => membership.groupId === group.id)
            .sort((left, right) => {
                if (left.position !== null && right.position !== null && left.position !== right.position)
                    return left.position - right.position;
                if (left.position !== null) return -1;
                if (right.position !== null) return 1;
                return (left.name || left.symbol).localeCompare(right.name || right.symbol, "zh-CN");
            })
            .map((membership, position) => ({ ...membership, position })),
    );
    return { schemaVersion: 1, groups, memberships: orderedMemberships };
}

function assertGroupName(state, rawName, currentGroupId = null) {
    const name = String(rawName || "")
        .normalize("NFKC")
        .trim();
    if (!name || name.length > 24) throw new Error("分类名称需为 1–24 个字符");
    if (
        state.groups.some(
            (group) =>
                group.id !== currentGroupId &&
                group.name.toLocaleLowerCase("zh-CN") === name.toLocaleLowerCase("zh-CN"),
        )
    )
        throw new Error("分类名称已存在");
    return name;
}

export function createWatchlistGroup(state, rawName, id = crypto.randomUUID()) {
    const current = normalizeWatchlistSnapshot(state);
    if (current.groups.length >= MAX_GROUPS) throw new Error(`最多创建 ${MAX_GROUPS} 个分类`);
    const now = new Date().toISOString();
    const group = {
        id,
        name: assertGroupName(current, rawName),
        position: current.groups.length,
        protected: false,
        createdAt: now,
        updatedAt: now,
    };
    return { state: { ...current, groups: [...current.groups, group] }, group };
}

export function renameWatchlistGroup(state, groupId, rawName) {
    const current = normalizeWatchlistSnapshot(state);
    const group = current.groups.find((item) => item.id === groupId);
    if (!group) throw new Error("自选分类不存在");
    if (group.protected) throw new Error("默认分类不能重命名");
    const name = assertGroupName(current, rawName, groupId);
    return {
        ...current,
        groups: current.groups.map((item) =>
            item.id === groupId ? { ...item, name, updatedAt: new Date().toISOString() } : item,
        ),
    };
}

export function deleteWatchlistGroup(state, groupId) {
    const current = normalizeWatchlistSnapshot(state);
    const group = current.groups.find((item) => item.id === groupId);
    if (!group) throw new Error("自选分类不存在");
    if (group.protected) throw new Error("默认分类不能删除");
    const retained = current.memberships.filter((item) => item.groupId !== groupId);
    const defaultSymbols = new Set(
        retained.filter((item) => item.groupId === DEFAULT_WATCHLIST_GROUP.id).map((item) => item.symbol),
    );
    const moved = current.memberships
        .filter((item) => item.groupId === groupId && !defaultSymbols.has(item.symbol))
        .map((item) => ({ ...item, groupId: DEFAULT_WATCHLIST_GROUP.id }));
    return normalizeWatchlistSnapshot({
        ...current,
        groups: current.groups.filter((item) => item.id !== groupId),
        memberships: [...retained, ...moved],
    });
}

export function addWatchlistMembers(state, groupId, stocks) {
    const current = normalizeWatchlistSnapshot(state);
    if (!current.groups.some((group) => group.id === groupId)) throw new Error("自选分类不存在");
    const groupMembers = current.memberships.filter((item) => item.groupId === groupId);
    const existing = new Set(groupMembers.map((item) => item.symbol));
    const currentCount = existing.size;
    const candidates = [];
    for (const stock of stocks) {
        if (
            !stock ||
            typeof stock.symbol !== "string" ||
            !SYMBOL_PATTERN.test(stock.symbol) ||
            existing.has(stock.symbol)
        )
            continue;
        existing.add(stock.symbol);
        candidates.push({
            groupId,
            symbol: stock.symbol,
            name: typeof stock.name === "string" ? stock.name.slice(0, 80) : "",
            createdAt: new Date().toISOString(),
            position: currentCount + candidates.length,
        });
    }
    if (currentCount + candidates.length > MAX_MEMBERS_PER_GROUP)
        throw new Error(`每个分类最多保存 ${MAX_MEMBERS_PER_GROUP} 只股票`);
    const alphabetical = [...groupMembers].sort((left, right) =>
        (left.name || left.symbol).localeCompare(right.name || right.symbol, "zh-CN"),
    );
    const manuallyOrdered = groupMembers.some((member, index) => member.symbol !== alphabetical[index].symbol);
    const nextMembers = manuallyOrdered
        ? [...groupMembers, ...candidates]
        : [...groupMembers, ...candidates].sort((left, right) =>
              (left.name || left.symbol).localeCompare(right.name || right.symbol, "zh-CN"),
          );
    return {
        state: {
            ...current,
            memberships: [
                ...current.memberships.filter((item) => item.groupId !== groupId),
                ...nextMembers.map((member, position) => ({ ...member, position })),
            ],
        },
        added: candidates.length,
        duplicates: stocks.length - candidates.length,
    };
}

export function removeWatchlistMember(state, groupId, symbol) {
    const current = normalizeWatchlistSnapshot(state);
    return {
        ...current,
        memberships: current.memberships.filter((item) => item.groupId !== groupId || item.symbol !== symbol),
    };
}

export function reorderWatchlistMembers(state, groupId, symbols) {
    const current = normalizeWatchlistSnapshot(state);
    const members = current.memberships.filter((member) => member.groupId === groupId);
    const bySymbol = new Map(members.map((member) => [member.symbol, member]));
    if (
        symbols.length !== members.length ||
        new Set(symbols).size !== members.length ||
        symbols.some((symbol) => !bySymbol.has(symbol))
    ) {
        throw new Error("自选股顺序已变化，请重试");
    }
    const reordered = symbols.map((symbol, position) => ({ ...bySymbol.get(symbol), position }));
    return {
        ...current,
        memberships: [...current.memberships.filter((member) => member.groupId !== groupId), ...reordered],
    };
}

function orderedMembers(state, groupId, universe) {
    const universeBySymbol = new Map(universe.map((stock) => [stock.symbol, stock]));
    return state.memberships
        .filter((membership) => membership.groupId === groupId)
        .map((membership) => ({ ...membership, stock: universeBySymbol.get(membership.symbol) }));
}

export function firstAvailableWatchlistSymbol(state, groupId, universe) {
    return (
        orderedMembers(state, groupId, universe).find((member) => member.stock?.has_data !== false && member.stock)
            ?.symbol || ""
    );
}

function openWatchlistDatabase(indexedDBFactory = globalThis.indexedDB) {
    if (!indexedDBFactory) return Promise.reject(new Error("当前浏览器不支持 IndexedDB"));
    return new Promise((resolve, reject) => {
        const request = indexedDBFactory.open(DATABASE_NAME, DATABASE_VERSION);
        request.onupgradeneeded = () => {
            const database = request.result;
            if (!database.objectStoreNames.contains(GROUP_STORE)) {
                const groups = database.createObjectStore(GROUP_STORE, { keyPath: "id" });
                groups.createIndex("by-position", "position", { unique: false });
            }
            if (!database.objectStoreNames.contains(MEMBERSHIP_STORE)) {
                const memberships = database.createObjectStore(MEMBERSHIP_STORE, {
                    keyPath: ["groupId", "symbol"],
                });
                memberships.createIndex("by-group", "groupId", { unique: false });
                memberships.createIndex("by-symbol", "symbol", { unique: false });
            }
        };
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error || new Error("自选股数据初始化失败"));
        request.onblocked = () => reject(new Error("自选股数据库升级被其他页面阻止，请关闭旧页面后重试"));
    });
}

function requestValue(request) {
    return new Promise((resolve, reject) => {
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error || new Error("自选股数据读取失败"));
    });
}

function waitForTransaction(transaction) {
    return new Promise((resolve, reject) => {
        transaction.oncomplete = resolve;
        transaction.onerror = () => reject(transaction.error || new Error("自选股数据保存失败"));
        transaction.onabort = () => reject(transaction.error || new Error("自选股数据保存已取消"));
    });
}

export const watchlistStorage = {
    async load() {
        const database = await openWatchlistDatabase();
        try {
            const transaction = database.transaction([GROUP_STORE, MEMBERSHIP_STORE], "readonly");
            const [groups, memberships] = await Promise.all([
                requestValue(transaction.objectStore(GROUP_STORE).getAll()),
                requestValue(transaction.objectStore(MEMBERSHIP_STORE).getAll()),
            ]);
            return normalizeWatchlistSnapshot({ groups, memberships });
        } finally {
            database.close();
        }
    },

    async save(snapshot) {
        const normalized = normalizeWatchlistSnapshot(snapshot);
        const database = await openWatchlistDatabase();
        try {
            const transaction = database.transaction([GROUP_STORE, MEMBERSHIP_STORE], "readwrite");
            const groups = transaction.objectStore(GROUP_STORE);
            const memberships = transaction.objectStore(MEMBERSHIP_STORE);
            groups.clear();
            memberships.clear();
            normalized.groups.forEach((group) => groups.put(group));
            normalized.memberships.forEach((membership) => memberships.put(membership));
            await waitForTransaction(transaction);
        } finally {
            database.close();
        }
    },
};

export class Watchlists {
    constructor({ onSelect, storage = watchlistStorage }) {
        this.onSelect = onSelect;
        this.storage = storage;
        this.state = normalizeWatchlistSnapshot(null);
        this.universe = [];
        this.selectedSymbol = "";
        this.selectedGroupId = DEFAULT_WATCHLIST_GROUP.id;
        this.railCollapsed = false;
        this.available = false;
        this.backtestStatuses = {};
        this.backtestFailures = {};
        this.backtestFillCounts = {};
        this.backtestReturns = {};
        this.backtestEligible = null;
        this.reordering = false;
        this.$ = (id) => document.getElementById(id);
        this.bind();
    }

    bind() {
        this.$("watchlist-group-select").addEventListener("change", (event) => {
            this.selectedGroupId = event.target.value;
            try {
                localStorage.setItem(SELECTED_GROUP_KEY, this.selectedGroupId);
            } catch {
                // 当前会话仍可使用；分类数据本身继续由 IndexedDB 持久化。
            }
            this.render();
            window.dispatchEvent(new CustomEvent("wavequant:watchlists-changed"));
        });
        this.$("watchlist-toggle-current").addEventListener("click", () => {
            const stock = this.universe.find((item) => item.symbol === this.selectedSymbol);
            if (!stock) return;
            if (this.has(stock.symbol)) void this.remove(stock.symbol);
            else void this.addStocks([stock]);
        });
        this.$("watchlist-rail-toggle").addEventListener("click", () => this.setRailCollapsed(!this.railCollapsed));
        this.$("watchlist-group-add").addEventListener("click", () => this.openEditor("create"));
        this.$("watchlist-group-rename").addEventListener("click", () => this.openEditor("rename"));
        this.$("watchlist-group-delete").addEventListener("click", () => void this.deleteSelectedGroup());
        this.$("watchlist-group-cancel").addEventListener("click", () => this.closeEditor());
        this.$("watchlist-group-form").addEventListener("submit", (event) => {
            event.preventDefault();
            void this.saveEditor();
        });
    }

    async init() {
        try {
            this.railCollapsed = localStorage.getItem(COLLAPSED_RAIL_KEY) === "true";
        } catch {
            // 折叠偏好不可用不影响自选数据。
        }
        this.renderRailState();
        try {
            this.state = await this.storage.load();
            await this.storage.save(this.state);
            this.available = true;
            try {
                const saved = localStorage.getItem(SELECTED_GROUP_KEY);
                if (saved && this.state.groups.some((group) => group.id === saved)) this.selectedGroupId = saved;
            } catch {
                // 选中分类偏好不可用不影响 IndexedDB 中的自选数据。
            }
            this.announce("自选股已从浏览器本地数据加载");
        } catch (error) {
            this.available = false;
            this.announce(`自选股存储不可用：${error.message}`);
        }
        this.render();
    }

    setUniverse(stocks, selectedSymbol) {
        this.universe = Array.isArray(stocks) ? stocks : [];
        this.selectedSymbol = selectedSymbol || "";
        this.render();
    }

    setSelected(symbol) {
        this.selectedSymbol = symbol;
        this.render();
    }

    get selectedGroup() {
        return this.state.groups.find((group) => group.id === this.selectedGroupId) || this.state.groups[0];
    }

    firstAvailableSymbol(stocks) {
        return firstAvailableWatchlistSymbol(this.state, this.selectedGroup.id, stocks);
    }

    orderedAvailableMembers(stocks = this.universe) {
        return orderedMembers(this.state, this.selectedGroup.id, stocks).filter(
            (member) => member.stock && member.stock.has_data !== false,
        );
    }

    setBacktestStatuses(statuses, eligibleSymbols = null, failures = {}, fillCounts = {}, returns = {}) {
        this.backtestStatuses = statuses;
        this.backtestEligible = eligibleSymbols;
        this.backtestFailures = failures;
        this.backtestFillCounts = fillCounts;
        this.backtestReturns = returns;
        for (const row of this.$("watchlist-stock-list").querySelectorAll(".watchlist-stock-row")) {
            this.renderBacktestStatus(row, row.dataset.symbol);
        }
    }

    renderBacktestStatus(row, symbol) {
        const badge = row.querySelector(".watchlist-backtest-badge");
        if (!badge) return;
        const status =
            this.backtestStatuses[symbol] ||
            (this.backtestEligible && !this.backtestEligible.has(symbol) ? "unavailable" : "pending");
        badge.dataset.status = status;
        const fills = this.backtestFillCounts[symbol];
        badge.textContent =
            status === "completed" && Number.isInteger(fills)
                ? `已完成 · ${fills}笔成交`
                : {
                      pending: "待回测",
                      historical: "已回测 · 待更新",
                      running: "回测中",
                      unknown: "状态待确认",
                      completed: "已完成",
                      failed: "失败",
                      unavailable: "无数据",
                  }[status];
        badge.title =
            status === "failed"
                ? this.backtestFailures[symbol] || "回测失败"
                : status === "historical"
                  ? "服务器有历史回测记录，但数据源、日期、策略版本或参数与当前设置不一致；本轮仍待更新"
                  : status === "completed" && fills === 0
                    ? "回测已完成，但没有实际模拟成交，图上不会有 B / S 成交标记"
                    : badge.textContent;
        const result = row.querySelector(".watchlist-backtest-result");
        const value = this.backtestReturns[symbol];
        const hasReturn = status === "completed" && Number.isFinite(value?.rate) && Number.isFinite(value?.amount);
        if (result) {
            result.hidden = !hasReturn;
            if (hasReturn) {
                result.dataset.result = value.rate > 0 ? "profit" : value.rate < 0 ? "loss" : "flat";
                result.textContent = `${value.rate > 0 ? "盈 +" : value.rate < 0 ? "亏 " : "平 "}${(value.rate * 100).toFixed(2)}%`;
                result.title = `期末盈亏 ${value.amount > 0 ? "+" : ""}${value.amount.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} 元；收益率 ${value.rate > 0 ? "+" : ""}${(value.rate * 100).toFixed(2)}%`;
            }
        }
        const open = row.querySelector(".watchlist-stock-open");
        if (open)
            open.setAttribute(
                "aria-label",
                `${open.dataset.baseLabel}，${badge.textContent}${hasReturn ? `，${result.title}` : ""}${status === "failed" ? `：${badge.title}` : ""}`,
            );
    }

    rememberSelectedGroup() {
        try {
            localStorage.setItem(SELECTED_GROUP_KEY, this.selectedGroupId);
        } catch {
            // 当前会话仍可使用；分类数据本身继续由 IndexedDB 持久化。
        }
    }

    setRailCollapsed(collapsed) {
        this.railCollapsed = Boolean(collapsed);
        try {
            localStorage.setItem(COLLAPSED_RAIL_KEY, String(this.railCollapsed));
        } catch {
            // 当前页面仍可折叠；偏好不可用不影响自选数据。
        }
        this.renderRailState();
    }

    renderRailState() {
        const rail = this.$("watchlist-rail");
        const body = this.$("watchlist-rail-body");
        const toggle = this.$("watchlist-rail-toggle");
        const workspace = rail.closest(".workspace-grid");
        rail.dataset.collapsed = String(this.railCollapsed);
        body.hidden = this.railCollapsed;
        toggle.setAttribute("aria-expanded", String(!this.railCollapsed));
        toggle.setAttribute("aria-label", this.railCollapsed ? "展开自选股列表" : "收起自选股列表");
        toggle.title = this.railCollapsed ? "展开自选股列表" : "收起自选股列表";
        workspace?.classList.toggle("watchlist-rail-collapsed", this.railCollapsed);
    }

    has(symbol) {
        return this.state.memberships.some(
            (membership) => membership.groupId === this.selectedGroup.id && membership.symbol === symbol,
        );
    }

    async commit(nextState, message, beforeRender) {
        try {
            await this.storage.save(nextState);
            this.state = normalizeWatchlistSnapshot(nextState);
            this.available = true;
            if (beforeRender) await beforeRender;
            this.render();
            this.announce(message);
            window.dispatchEvent(new CustomEvent("wavequant:watchlists-changed"));
            return true;
        } catch (error) {
            this.announce(`保存失败：${error.message}`);
            return false;
        }
    }

    async addStocks(stocks) {
        if (!this.available) {
            this.announce("自选股存储当前不可用，请刷新页面后重试");
            return { added: 0, duplicates: stocks.length };
        }
        const result = addWatchlistMembers(this.state, this.selectedGroup.id, stocks);
        if (!result.added) {
            this.announce(`所选股票已在“${this.selectedGroup.name}”中`);
            return result;
        }
        const saved = await this.commit(
            result.state,
            `已将 ${result.added} 只股票加入“${this.selectedGroup.name}”${result.duplicates ? `，跳过 ${result.duplicates} 只重复股票` : ""}`,
        );
        return saved ? result : { added: 0, duplicates: stocks.length };
    }

    async remove(symbol) {
        await this.commit(removeWatchlistMember(this.state, this.selectedGroup.id, symbol), "已从当前分类移除股票");
    }

    async saveOrder(symbols, focusSymbol = "") {
        if (this.reordering) return;
        this.reordering = true;
        try {
            const saved = await this.commit(
                reorderWatchlistMembers(this.state, this.selectedGroup.id, symbols),
                "自选股顺序已保存",
                this.orderAnimation,
            );
            if (!saved) this.render();
            if (focusSymbol) {
                const handle = [...this.$("watchlist-stock-list").querySelectorAll(".watchlist-stock-drag")].find(
                    (item) => item.closest(".watchlist-stock-row")?.dataset.symbol === focusSymbol,
                );
                handle?.focus();
            }
        } finally {
            this.reordering = false;
        }
    }

    animateOrder(list, rearrange) {
        const rows = [...list.querySelectorAll(".watchlist-stock-row")];
        const before = new Map(rows.map((item) => [item, item.getBoundingClientRect().top]));
        rows.forEach((item) => item.getAnimations().forEach((animation) => animation.cancel()));
        rearrange();
        if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
            this.orderAnimation = Promise.resolve();
            return;
        }
        const animations = rows.flatMap((item) => {
            const distance = before.get(item) - item.getBoundingClientRect().top;
            if (Math.abs(distance) < 1) return [];
            return [
                item.animate([{ transform: `translateY(${distance}px)` }, { transform: "translateY(0)" }], {
                    duration: 220,
                    easing: "cubic-bezier(0.22, 1, 0.36, 1)",
                }),
            ];
        });
        this.orderAnimation = Promise.allSettled(animations.map((animation) => animation.finished));
    }

    bindOrderHandle(handle, row, list) {
        handle.addEventListener("keydown", (event) => {
            if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
            event.preventDefault();
            if (this.reordering) return;
            const rows = [...list.querySelectorAll(".watchlist-stock-row")];
            const currentIndex = rows.indexOf(row);
            const nextIndex = currentIndex + (event.key === "ArrowUp" ? -1 : 1);
            if (nextIndex < 0 || nextIndex >= rows.length) return;
            this.animateOrder(list, () =>
                list.insertBefore(row, event.key === "ArrowUp" ? rows[nextIndex] : rows[nextIndex].nextSibling),
            );
            void this.saveOrder(
                [...list.querySelectorAll(".watchlist-stock-row")].map((item) => item.dataset.symbol),
                row.dataset.symbol,
            );
        });
        handle.addEventListener("pointerdown", (event) => {
            if (event.button !== 0 || this.reordering || list.childElementCount < 2) return;
            event.preventDefault();
            const initialOrder = [...list.querySelectorAll(".watchlist-stock-row")].map((item) => item.dataset.symbol);
            let moved = false;
            let ghost = null;
            let origin = null;
            const lift = () => {
                origin = row.getBoundingClientRect();
                ghost = row.cloneNode(true);
                ghost.classList.add("watchlist-stock-ghost");
                ghost.setAttribute("aria-hidden", "true");
                ghost.inert = true;
                Object.assign(ghost.style, {
                    left: `${origin.left}px`,
                    top: `${origin.top}px`,
                    width: `${origin.width}px`,
                    height: `${origin.height}px`,
                });
                (list.closest("[data-wavequant-react-workbench]") || document.body).append(ghost);
                row.dataset.dragging = "true";
            };
            const move = (moveEvent) => {
                if (moveEvent.pointerId !== event.pointerId) return;
                if (!row.isConnected) return;
                if (Math.abs(moveEvent.clientY - event.clientY) < 4 && !moved) return;
                if (!moved) {
                    moved = true;
                    lift();
                }
                ghost.style.transform = `translate3d(0, ${moveEvent.clientY - event.clientY}px, 0)`;
                const others = [...list.querySelectorAll(".watchlist-stock-row")].filter((item) => item !== row);
                const before = others.find(
                    (item) => moveEvent.clientY < item.getBoundingClientRect().top + item.offsetHeight / 2,
                );
                if (before !== row.nextElementSibling && (before || row !== list.lastElementChild))
                    this.animateOrder(list, () => list.insertBefore(row, before || null));
                const bounds = list.getBoundingClientRect();
                if (moveEvent.clientY < bounds.top + 24) list.scrollTop -= 12;
                if (moveEvent.clientY > bounds.bottom - 24) list.scrollTop += 12;
            };
            const finish = (save) => {
                window.removeEventListener("pointermove", move);
                window.removeEventListener("pointerup", up);
                window.removeEventListener("pointercancel", cancel);
                if (!row.isConnected) {
                    ghost?.remove();
                    return;
                }
                if (!moved) return;
                if (!save) {
                    const rows = new Map(
                        [...list.querySelectorAll(".watchlist-stock-row")].map((item) => [item.dataset.symbol, item]),
                    );
                    this.animateOrder(list, () => initialOrder.forEach((symbol) => list.append(rows.get(symbol))));
                }
                row.getAnimations().forEach((animation) => animation.finish());
                const target = row.getBoundingClientRect();
                const landing = window.matchMedia("(prefers-reduced-motion: reduce)").matches
                    ? Promise.resolve()
                    : ghost.animate(
                          [
                              { transform: ghost.style.transform },
                              {
                                  transform: `translate3d(${target.left - origin.left}px, ${target.top - origin.top}px, 0)`,
                              },
                          ],
                          { duration: 160, easing: "cubic-bezier(0.22, 1, 0.36, 1)" },
                      ).finished;
                this.orderAnimation = Promise.allSettled([this.orderAnimation, landing]).then(() => {
                    ghost.remove();
                    delete row.dataset.dragging;
                });
                const symbols = [...list.querySelectorAll(".watchlist-stock-row")].map((item) => item.dataset.symbol);
                if (save && symbols.some((symbol, index) => symbol !== initialOrder[index]))
                    void this.saveOrder(symbols);
            };
            const up = (upEvent) => {
                if (upEvent.pointerId === event.pointerId) finish(true);
            };
            const cancel = (cancelEvent) => {
                if (cancelEvent.pointerId === event.pointerId) finish(false);
            };
            window.addEventListener("pointermove", move);
            window.addEventListener("pointerup", up);
            window.addEventListener("pointercancel", cancel);
        });
    }

    openEditor(mode) {
        const form = this.$("watchlist-group-form");
        const input = this.$("watchlist-group-name");
        form.dataset.mode = mode;
        form.hidden = false;
        this.$("watchlist-group-editor-title").textContent = mode === "create" ? "新建分类" : "重命名分类";
        input.value = mode === "create" ? "" : this.selectedGroup.name;
        input.focus();
        input.select();
    }

    closeEditor() {
        this.$("watchlist-group-form").hidden = true;
        this.$("watchlist-group-name").value = "";
    }

    async saveEditor() {
        try {
            const form = this.$("watchlist-group-form");
            const name = this.$("watchlist-group-name").value;
            if (form.dataset.mode === "create") {
                const created = createWatchlistGroup(this.state, name);
                if (await this.commit(created.state, `已创建分类“${created.group.name}”`)) {
                    this.selectedGroupId = created.group.id;
                    this.rememberSelectedGroup();
                }
            } else {
                const renamed = renameWatchlistGroup(this.state, this.selectedGroup.id, name);
                await this.commit(renamed, `分类已重命名为“${name.normalize("NFKC").trim()}”`);
            }
            this.closeEditor();
            this.render();
        } catch (error) {
            this.announce(error.message);
            this.$("watchlist-group-name").focus();
        }
    }

    async deleteSelectedGroup() {
        const group = this.selectedGroup;
        if (group.protected) return;
        const memberCount = this.state.memberships.filter((item) => item.groupId === group.id).length;
        if (
            !confirm(
                memberCount
                    ? `删除“${group.name}”？其中 ${memberCount} 只股票会移入“${DEFAULT_WATCHLIST_GROUP.name}”。`
                    : `删除空分类“${group.name}”？`,
            )
        )
            return;
        const next = deleteWatchlistGroup(this.state, group.id);
        if (await this.commit(next, `已删除“${group.name}”，其中股票已保留`)) {
            this.selectedGroupId = DEFAULT_WATCHLIST_GROUP.id;
            this.rememberSelectedGroup();
            this.render();
        }
    }

    announce(message) {
        this.$("watchlist-status").textContent = message;
    }

    render() {
        const group = this.selectedGroup;
        const select = this.$("watchlist-group-select");
        const previous = select.value;
        select.replaceChildren();
        this.state.groups.forEach((item) => {
            const option = document.createElement("option");
            const count = this.state.memberships.filter((membership) => membership.groupId === item.id).length;
            option.value = item.id;
            option.textContent = `${item.name}（${count}）`;
            select.append(option);
        });
        select.value = this.state.groups.some((item) => item.id === this.selectedGroupId)
            ? this.selectedGroupId
            : previous || DEFAULT_WATCHLIST_GROUP.id;
        this.selectedGroupId = select.value;
        select.disabled = !this.available;
        this.$("watchlist-group-add").disabled = !this.available || this.state.groups.length >= MAX_GROUPS;
        this.$("watchlist-group-rename").disabled = !this.available || group.protected;
        this.$("watchlist-group-delete").disabled = !this.available || group.protected;

        const currentStock = this.universe.find((item) => item.symbol === this.selectedSymbol);
        const alreadyAdded = currentStock ? this.has(currentStock.symbol) : false;
        const star = this.$("watchlist-toggle-current");
        star.disabled = !this.available || !currentStock;
        star.setAttribute("aria-pressed", String(alreadyAdded));
        star.setAttribute(
            "aria-label",
            alreadyAdded
                ? `从“${group.name}”移除 ${currentStock?.name || currentStock?.symbol || "当前股票"}`
                : `将 ${currentStock?.name || currentStock?.symbol || "当前股票"} 加入“${group.name}”`,
        );
        star.title = alreadyAdded ? `已在“${group.name}”中，点击移除` : `加入“${group.name}”`;
        setWatchlistStarIcon(star, alreadyAdded);

        const members = orderedMembers(this.state, group.id, this.universe);
        this.$("watchlist-count").textContent = `${members.length} 只`;
        this.renderRailState();
        const list = this.$("watchlist-stock-list");
        list.replaceChildren();
        if (!members.length) {
            const empty = document.createElement("p");
            empty.className = "stock-empty";
            empty.textContent = "当前分类还没有股票，可添加当前股票或从结构结果一键加入。";
            list.append(empty);
            return;
        }
        for (const member of members) {
            const row = document.createElement("div");
            row.className = "watchlist-stock-row";
            row.dataset.symbol = member.symbol;
            const drag = document.createElement("button");
            drag.type = "button";
            drag.className = "watchlist-stock-drag";
            const dragIcon = document.getElementById("watchlist-drag-icon")?.cloneNode(true);
            if (dragIcon) {
                dragIcon.removeAttribute("id");
                dragIcon.setAttribute("aria-hidden", "true");
                drag.append(dragIcon);
            }
            drag.setAttribute("aria-label", `拖拽调整 ${member.name || member.symbol} 的顺序，或按上下方向键移动`);
            drag.title = "拖动排序；方向键上下移动";
            this.bindOrderHandle(drag, row, list);
            const open = document.createElement("button");
            open.type = "button";
            open.className = "watchlist-stock-open";
            open.setAttribute("aria-pressed", String(member.symbol === this.selectedSymbol));
            const canOpen = Boolean(member.stock && member.stock.has_data !== false);
            open.disabled = !canOpen;
            open.setAttribute(
                "aria-label",
                canOpen
                    ? `切换到 ${member.name || member.symbol}`
                    : `${member.name || member.symbol}，当前数据源不可用`,
            );
            open.dataset.baseLabel = open.getAttribute("aria-label");
            if (!canOpen) open.title = "当前数据源不可用";
            const name = document.createElement("strong");
            name.textContent = member.stock?.name || member.name || member.symbol;
            const code = document.createElement("small");
            code.textContent = member.symbol;
            const identity = document.createElement("span");
            identity.className = "watchlist-stock-identity";
            identity.append(name, code);
            const backtestStatus = document.createElement("span");
            backtestStatus.className = "watchlist-backtest-badge";
            const result = document.createElement("span");
            result.className = "watchlist-backtest-result";
            result.hidden = true;
            const meta = document.createElement("span");
            meta.className = "watchlist-stock-meta";
            meta.append(backtestStatus, result);
            open.append(identity, meta);
            open.addEventListener("click", () => this.onSelect(member.symbol));
            row.append(drag, open);
            this.renderBacktestStatus(row, member.symbol);
            list.append(row);
        }
    }
}
