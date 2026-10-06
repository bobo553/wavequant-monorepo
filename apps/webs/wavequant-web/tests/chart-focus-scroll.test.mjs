import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { runInNewContext } from "node:vm";

import { JSDOM } from "jsdom";
import ts from "typescript";

import { scrollChartWithMetrics } from "../public/chart-focus-scroll.js";

const runtime = readFileSync(new URL("../public/app.js", import.meta.url), "utf8");
function appFunction(name, next) {
    return runtime.slice(runtime.indexOf(`function ${name}(`), runtime.indexOf(`\nfunction ${next}(`));
}

function harness({ viewportHeight = 900, headerHeight = 56 } = {}) {
    const dom = new JSDOM(
        '<div class="wavequant-shell" data-shell-variant="research"><header></header><main><section class="metric-grid"><strong id="metric-return">12.5%</strong></section><article class="chart-card"><div id="price-chart"></div></article><div id="selection-info"></div><input id="show-fills"></main></div>',
    );
    const doc = dom.window.document;
    dom.window.innerHeight = viewportHeight;
    const calls = [];
    let scrollY = 1600;
    doc.querySelector("header").getBoundingClientRect = () => ({ height: headerHeight });
    const positions = { "price-chart": [400, 604], "selection-info": [420, 400], "metric-return": [190, 26] };
    const metrics = doc.querySelector(".metric-grid");
    metrics.getBoundingClientRect = () => ({ top: 175 - scrollY, height: 60 });
    for (const [id, [top, height]] of Object.entries(positions)) {
        doc.getElementById(id).getBoundingClientRect = () => ({ top: top - scrollY, height });
    }
    dom.window.Element.prototype.scrollIntoView = function (options) {
        const bounds = this.getBoundingClientRect();
        const absoluteTop = bounds.top + scrollY;
        scrollY =
            options.block === "center"
                ? absoluteTop + bounds.height / 2 - viewportHeight / 2
                : absoluteTop - Number.parseFloat(this.style.scrollMarginTop || "0");
        calls.push({ element: this, options });
    };
    const chart = {
        setAnnotationOptions: () => {},
        selectAnnotation: (id) => calls.push({ selected: id }),
        flashSelectedAnnotation: (id, stage) => calls.push({ flashed: id, stage }),
        focusRange: (from, to) => {
            calls.push({ from, to });
            return true;
        },
    };
    const context = {
        $: (id) => doc.getElementById(id),
        chart,
        showPage: () => {},
        setChartView: () => {},
        annotationOptions: () => ({}),
        requestAnimationFrame: (callback) => callback(),
        showBacktestToast: () => {},
        scrollChartWithMetrics,
    };
    return { dom, doc, calls, context };
}

function assertReturnVisible(doc, viewportHeight = 900, headerHeight = 56) {
    const ratio = doc.getElementById("metric-return").getBoundingClientRect();
    assert.ok(
        ratio.top >= headerHeight && ratio.top + ratio.height <= viewportHeight,
        `净收益被滚出可视区域：${ratio.top}`,
    );
    assert.equal(doc.getElementById("metric-return").textContent, "12.5%");
}

for (const [description, name, next, argument] of [
    ["成交节点定位", "selectFill", "selectBlockedNode", '{ id: "fill" }, true'],
    ["成交账本查看原因", "selectFill", "selectBlockedNode", '{ id: "fill" }'],
    ["被拦截记录定位", "selectBlockedNode", "exportBacktest", '{ id: "blocked", blockedStage: "screening" }'],
    ["最大回撤持仓定位", "focusHoldingInterval", "focusMaximumDrawdown", '{ from: "2025-04-24", to: "2025-04-25" }'],
]) {
    test(`${description}滚动后个股净收益仍在导航下方`, () => {
        const { dom, doc, context, calls } = harness();
        try {
            runInNewContext(`${appFunction(name, next)}\n${name}(${argument});`, context);
            assertReturnVisible(doc);
            assert.ok(calls.some((call) => call.selected || call.from));
            assert.equal(calls.filter((call) => call.element).length, 1);
        } finally {
            dom.window.close();
        }
    });
}

test("最新成交和跨日期订单定位完成后都保留净收益", () => {
    const source = ts.createSourceFile("app.js", runtime, ts.ScriptTarget.Latest, true, ts.ScriptKind.JS);
    let branch;
    const visit = (node) => {
        if (
            ts.isIfStatement(node) &&
            node.expression.getText(source) === "state.pendingFocus" &&
            node.getText(source).includes('detail("已定位"')
        )
            branch = node.getText(source);
        ts.forEachChild(node, visit);
    };
    visit(source);
    assert.ok(branch);
    for (const pendingFocus of [null, { time: "2025-04-25", description: "订单定位" }]) {
        const { dom, doc, calls, context } = harness();
        Object.assign(context, {
            state: { pendingFocus },
            data: { markers: [{ kind: "fill", id: "latest" }] },
            focusLatestFill: true,
            detail: () => {},
        });
        context.chart.focus = (time) => calls.push({ focused: time });
        try {
            runInNewContext(branch, context);
            assertReturnVisible(doc);
            assert.ok(calls.some((call) => call.selected === "latest" || call.focused === "2025-04-25"));
        } finally {
            dom.window.close();
        }
    }
});

test("仅看成交定位和ABC定位共用收益可见的滚动", () => {
    for (const [id, boundary] of [
        ["fills-only", "async function loadHealth("],
        ["focus-abc", '$("focus-fill").addEventListener'],
    ]) {
        const { dom, doc, calls, context } = harness();
        for (const control of [
            id,
            "show-markers",
            "show-entry-rejections",
            "show-rules",
            "show-tertiary-abc",
            "show-levels",
            "show-theory",
        ]) {
            const input = doc.createElement("input");
            input.id = control;
            doc.body.append(input);
        }
        Object.assign(context, {
            state: { theory: {}, view: { markers: [{ kind: "fill", id: "latest" }] } },
            updateLayerToggleCount: () => {},
            tradePlayback: { pause: () => {} },
        });
        context.chart.setTheory = () => {};
        context.chart.focusWaveProjection = () => true;
        const start = runtime.indexOf(`$("${id}").addEventListener("click",`);
        const end = runtime.indexOf(boundary, start);
        assert.ok(start >= 0 && end > start);
        try {
            runInNewContext(runtime.slice(start, end), context);
            doc.getElementById(id).click();
            assertReturnVisible(doc);
            assert.equal(calls.filter((call) => call.element).length, 1);
        } finally {
            dom.window.close();
        }
    }
});

test("滚动预留实际导航高度并适应不同视口", () => {
    for (const [viewportHeight, headerHeight] of [
        [640, 56],
        [768, 70],
        [1080, 98],
    ]) {
        const { dom, doc } = harness({ viewportHeight, headerHeight });
        try {
            scrollChartWithMetrics(doc.getElementById("price-chart"));
            assertReturnVisible(doc, viewportHeight, headerHeight);
        } finally {
            dom.window.close();
        }
    }
});

test("不含应用壳的页面仍保留收益，缺指标区则定位图表卡片", () => {
    const { dom, doc, calls } = harness();
    try {
        doc.querySelector("header").remove();
        scrollChartWithMetrics(doc.getElementById("price-chart"));
        assertReturnVisible(doc, 900, 0);
        doc.querySelector(".metric-grid").remove();
        scrollChartWithMetrics(doc.getElementById("price-chart"));
        assert.equal(calls.at(-1).element, doc.querySelector(".chart-card"));
        assert.equal(calls.at(-1).options.inline, "nearest");
    } finally {
        dom.window.close();
    }
});

test("没有卡片容器时仍能滚动图表本身", () => {
    const { dom, doc, calls } = harness();
    try {
        doc.querySelector(".metric-grid").remove();
        doc.body.append(doc.getElementById("price-chart"));
        scrollChartWithMetrics(doc.getElementById("price-chart"));
        assert.equal(calls.at(-1).element.id, "price-chart");
    } finally {
        dom.window.close();
    }
});
