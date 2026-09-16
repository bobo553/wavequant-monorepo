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
        });
    }
    return { schemaVersion: 1, groups, memberships };
}

function assertGroupName(state, rawName, currentGroupId = null) {
    const name = String(rawName || "").normalize("NFKC").trim();
    if (!name || name.length > 24) throw new Error("分类名称需为 1–24 个字符");
    if (
        state.groups.some(
            (group) =>
                group.id !== currentGroupId && group.name.toLocaleLowerCase("zh-CN") === name.toLocaleLowerCase("zh-CN"),
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
    const existing = new Set(
        current.memberships.filter((item) => item.groupId === groupId).map((item) => item.symbol),
    );
    const candidates = [];
    for (const stock of stocks) {
        if (!stock || typeof stock.symbol !== "string" || !SYMBOL_PATTERN.test(stock.symbol) || existing.has(stock.symbol))
            continue;
        existing.add(stock.symbol);
        candidates.push({
            groupId,
            symbol: stock.symbol,
            name: typeof stock.name === "string" ? stock.name.slice(0, 80) : "",
            createdAt: new Date().toISOString(),
        });
    }
    const currentCount = current.memberships.filter((item) => item.groupId === groupId).length;
    if (currentCount + candidates.length > MAX_MEMBERS_PER_GROUP)
        throw new Error(`每个分类最多保存 ${MAX_MEMBERS_PER_GROUP} 只股票`);
    return {
        state: { ...current, memberships: [...current.memberships, ...candidates] },
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
        toggle.textContent = this.railCollapsed ? "›" : "‹";
        workspace?.classList.toggle("watchlist-rail-collapsed", this.railCollapsed);
    }

    has(symbol) {
        return this.state.memberships.some(
            (membership) => membership.groupId === this.selectedGroup.id && membership.symbol === symbol,
        );
    }

    async commit(nextState, message) {
        try {
            await this.storage.save(nextState);
            this.state = normalizeWatchlistSnapshot(nextState);
            this.available = true;
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
        star.querySelector("span").textContent = alreadyAdded ? "★" : "☆";

        const universeBySymbol = new Map(this.universe.map((stock) => [stock.symbol, stock]));
        const members = this.state.memberships
            .filter((membership) => membership.groupId === group.id)
            .map((membership) => ({ ...membership, stock: universeBySymbol.get(membership.symbol) }))
            .sort((left, right) => (left.name || left.symbol).localeCompare(right.name || right.symbol, "zh-CN"));
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
            const open = document.createElement("button");
            open.type = "button";
            open.className = "watchlist-stock-open";
            open.setAttribute("aria-pressed", String(member.symbol === this.selectedSymbol));
            open.disabled = member.stock?.has_data === false || !member.stock;
            open.setAttribute("aria-label", `查看 ${member.name || member.symbol}`);
            const name = document.createElement("strong");
            name.textContent = member.stock?.name || member.name || member.symbol;
            const code = document.createElement("small");
            code.textContent = member.stock ? `${member.symbol} · 点击查看` : `${member.symbol} · 当前数据源不可用`;
            open.append(name, code);
            open.addEventListener("click", () => this.onSelect(member.symbol));
            const remove = document.createElement("button");
            remove.type = "button";
            remove.className = "watchlist-stock-remove";
            remove.textContent = "★";
            remove.setAttribute("aria-pressed", "true");
            remove.setAttribute("aria-label", `从“${group.name}”移除 ${member.name || member.symbol}`);
            remove.title = `已收藏到“${group.name}”，点击移除`;
            remove.addEventListener("click", () => void this.remove(member.symbol));
            row.append(open, remove);
            list.append(row);
        }
    }
}
