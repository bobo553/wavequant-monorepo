import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { JSDOM } from "jsdom";

const script = await readFile(new URL("../public/watchlist-first-paint.js", import.meta.url), "utf8");
const doc = (name = "海洋王", revision = 1) => ({ revision, settings: null, snapshot: {
    schemaVersion: 1,
    groups: [{ id: "default", name: "我的自选", position: 0, protected: true }],
    memberships: [{ groupId: "default", symbol: "sz.002724", name, position: 0 }],
} });
function dom() {
    const instance = new JSDOM(`<p id="watchlist-status">原始占位</p><span id="watchlist-count">0 只</span>
        <p id="watchlist-backtest-status">等待</p><div id="watchlist-first-paint"><p>原始占位</p></div>`,
        { url: "http://localhost:3003/research", runScripts: "outside-only" });
    instance.window.AbortSignal = AbortSignal;
    return instance;
}

test("local names are visible before React or a server response, without changing React light DOM", () => {
    const instance = dom();
    try {
        const { window } = instance;
        window.localStorage.setItem("wavequant.watchlists.preview.v2", JSON.stringify(doc()));
        window.fetch = () => new Promise(() => {});
        const host = window.document.getElementById("watchlist-first-paint");
        const original = host.innerHTML;
        window.eval(script);
        assert.match(host.shadowRoot.textContent, /海洋王/);
        assert.equal(host.innerHTML, original);
        assert.match(window.document.getElementById("watchlist-status").shadowRoot.textContent, /点击股票/);
        window.wavequantWatchlistBoot.runtimeReady = true;
        window.wavequantWatchlistBoot.release();
        assert.equal(host.shadowRoot.querySelector("slot").tagName, "SLOT");
    } finally { instance.window.close(); }
});

test("server cache miss falls back once and the full runtime reuses that database response", async () => {
    const instance = dom();
    try {
        const calls = [];
        instance.window.fetch = async (path) => {
            calls.push(path);
            return path.endsWith("only") ? new Response(null, { status: 503 }) : Response.json(doc());
        };
        instance.window.eval(script);
        const boot = instance.window.wavequantWatchlistBoot;
        await boot.cached;
        assert.match(instance.window.document.getElementById("watchlist-first-paint").shadowRoot.textContent, /海洋王/);
        await boot.loadFresh();
        assert.deepEqual(calls.filter((path) => path.startsWith("/api/watchlists")), ["/api/watchlists?cache=only", "/api/watchlists?cache=refresh"]);
    } finally { instance.window.close(); }
});

test("older server memory cannot roll back a newer local preview", async () => {
    const instance = dom();
    try {
        instance.window.localStorage.setItem("wavequant.watchlists.preview.v2", JSON.stringify(doc("新名称", 2)));
        instance.window.fetch = async () => Response.json(doc("旧名称", 1));
        instance.window.eval(script);
        await instance.window.wavequantWatchlistBoot.cached;
        assert.match(instance.window.document.getElementById("watchlist-first-paint").shadowRoot.textContent, /新名称/);
        assert.equal(instance.window.wavequantWatchlistBoot.preview.revision, 2);
    } finally { instance.window.close(); }
});

test("empty server cache is a valid empty list and does not trigger a database fallback", async () => {
    const instance = dom();
    try {
        const empty = doc(); empty.snapshot.memberships = [];
        const calls = [];
        instance.window.fetch = async (path) => { calls.push(path); return Response.json(empty); };
        instance.window.eval(script);
        await instance.window.wavequantWatchlistBoot.cached;
        assert.equal(calls.filter((path) => path.startsWith("/api/watchlists")).length, 1);
        assert.match(instance.window.document.getElementById("watchlist-first-paint").shadowRoot.textContent, /暂无自选/);
    } finally { instance.window.close(); }
});


test("watchlist boot never requests research metadata", async () => {
    const instance = dom();
    try {
        const calls = [];
        instance.window.fetch = async (path) => { calls.push(path); return Response.json(doc()); };
        instance.window.eval(script);
        await instance.window.wavequantWatchlistBoot.cached;
        assert.deepEqual(calls, ["/api/watchlists?cache=only"]);
        assert.equal(instance.window.wavequantWatchlistBoot.catalogResponse, undefined);
    } finally { instance.window.close(); }
});


test("cached stock selection works while configuration and React are still pending", () => {
    const instance = dom();
    try {
        const { window } = instance;
        window.localStorage.setItem("wavequant.watchlists.preview.v2", JSON.stringify(doc()));
        window.fetch = () => new Promise(() => {});
        window.eval(script);
        const boot = window.wavequantWatchlistBoot;
        const host = window.document.getElementById("watchlist-first-paint");
        host.shadowRoot.querySelector("button").click();
        assert.equal(boot.pendingSymbol, "sz.002724");
        assert.equal(host.shadowRoot.querySelector("button").getAttribute("aria-pressed"), "true");
        assert.match(window.document.getElementById("watchlist-status").shadowRoot.textContent, /已选中 海洋王/);
        boot.release();
        assert.ok(host.shadowRoot.querySelector("button"));
        boot.select("sz.000000");
        assert.equal(boot.pendingSymbol, "sz.002724");
        boot.runtimeReady = true;
        boot.release();
        assert.ok(host.shadowRoot.querySelector("slot"));
    } finally { instance.window.close(); }
});


test("classification and filtering are available before runtime ownership", () => {
    const instance = dom();
    try {
        const { window } = instance;
        const value = doc();
        value.snapshot.groups.push({ id: "travel", name: "旅游", position: 1, protected: false });
        value.snapshot.memberships.push({ groupId: "travel", symbol: "sz.000978", name: "桂林旅游", position: 0 });
        const groupHost = window.document.createElement("div");
        groupHost.id = "watchlist-group-first-paint";
        const search = window.document.createElement("input");
        search.id = "header-stock-search";
        window.document.body.append(groupHost, search);
        window.localStorage.setItem("wavequant.watchlists.preview.v2", JSON.stringify(value));
        window.fetch = () => new Promise(() => {});
        window.eval(script);
        const select = groupHost.shadowRoot.querySelector("select");
        select.value = "travel";
        select.dispatchEvent(new window.Event("change"));
        assert.equal(window.wavequantWatchlistBoot.groupId, "travel");
        assert.equal(window.localStorage.getItem("wavequant.watchlists.selected.v1"), "travel");
        const list = window.document.getElementById("watchlist-first-paint");
        assert.match(list.shadowRoot.textContent, /桂林旅游/);
        assert.doesNotMatch(list.shadowRoot.textContent, /海洋王/);
        search.value = "不存在";
        search.dispatchEvent(new window.Event("input", { bubbles: true }));
        assert.equal(list.shadowRoot.querySelectorAll("button").length, 0);
        search.value = "000978";
        search.dispatchEvent(new window.Event("input", { bubbles: true }));
        search.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
        assert.equal(window.wavequantWatchlistBoot.pendingSymbol, "sz.000978");
        assert.equal(window.wavequantWatchlistBoot.query, "000978");
    } finally { instance.window.close(); }
});
