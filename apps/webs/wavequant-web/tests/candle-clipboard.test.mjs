import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { setImmediate } from "node:timers/promises";
import { runInNewContext } from "node:vm";

import { JSDOM } from "jsdom";

import { bindCandleClipboard } from "../public/candle-clipboard.js";
import { candleCopyText, previousCandleClose } from "../public/candle-details.js";

const bar = Object.freeze({ time: "2026-10-10", open: 10, high: 12, low: 9, close: 11, volume: 100 });
const appSource = readFileSync(new URL("../public/app.js", import.meta.url), "utf8");

function harness(t, { bind = true, copyCandle } = {}) {
    const dom = new JSDOM(
        '<article class="chart-card" data-chart-view="primary"><div id="chart" tabindex="0"><canvas></canvas></div></article>' +
            '<aside id="tooltip"><span id="details">日期与收盘价</span><button id="card-copy">复制</button></aside>' +
            '<button id="copy-candle">复制 K 线</button><div id="candle-copy-feedback"></div><p id="outside">其他内容</p>' +
            '<select id="symbol-select"><option value="sh.600000">测试股票</option><option value="sz.000678">襄阳轴承</option></select>' +
            '<select id="result-scope"><option value="akshare">AkShare</option><option value="akshare-backtest">AkShare 回测</option><option value="tdx">通达信</option></select>' +
            '<div id="timeframe-select" data-value="1d"></div>' +
            '<div id="chart-loading-overlay" hidden></div>',
        { pretendToBeVisual: true },
    );
    const document = dom.window.document;
    const container = document.getElementById("chart");
    const tooltip = document.getElementById("tooltip");
    const h = {
        window: dom.window,
        document,
        container,
        tooltip,
        canvas: container.querySelector("canvas"),
        cardButton: document.getElementById("card-copy"),
        topButton: document.getElementById("copy-candle"),
        outside: document.getElementById("outside"),
        chart: { container, tooltip, tooltipBarTime: bar.time },
        containerHovered: false,
        tooltipHovered: false,
        candle: bar,
        enabled: true,
        copied: [],
        formatted: [],
        feedback: [],
        nativeWrites: [],
        asyncWrites: [],
        unbind: () => {},
    };
    for (const [region, property] of [
        [container, "containerHovered"],
        [tooltip, "tooltipHovered"],
    ]) {
        const matches = region.matches.bind(region);
        region.matches = (selector) => (selector === ":hover" ? h[property] : matches(selector));
    }
    const options = {
        getCandle: () => h.candle,
        isEnabled: () => h.enabled,
        copyText: (candle) => {
            h.formatted.push(candle);
            return `snapshot:${candle.time}:${candle.close}`;
        },
        copyCandle:
            copyCandle ??
            ((candle) => {
                h.copied.push(candle);
            }),
        onCopied: (candle) => h.feedback.push(candle),
    };
    if (bind) h.unbind = bindCandleClipboard(h.chart, options);
    t.after(() => {
        h.unbind();
        dom.window.close();
    });
    h.key = (target = h.canvas, init = {}) => {
        const event = new h.window.KeyboardEvent("keydown", {
            key: "c",
            ctrlKey: true,
            bubbles: true,
            cancelable: true,
            composed: true,
            ...init,
        });
        target.dispatchEvent(event);
        return event;
    };
    h.copy = (target = h.canvas, { clipboard = true, prevented = false } = {}) => {
        const event = new h.window.Event("copy", { bubbles: true, cancelable: true, composed: true });
        if (clipboard)
            Object.defineProperty(event, "clipboardData", {
                value: { setData: (type, text) => h.nativeWrites.push([type, text]) },
            });
        if (prevented) event.preventDefault();
        target.dispatchEvent(event);
        return event;
    };
    return h;
}

function assertUntouched(h, event) {
    assert.equal(event.defaultPrevented, false);
    assert.deepEqual(h.copied, []);
    assert.deepEqual(h.formatted, []);
    assert.deepEqual(h.feedback, []);
    assert.deepEqual(h.nativeWrites, []);
}

const externalControls = [
    ["stock search", '<input id="stock-search" type="search" value="襄阳轴承">'],
    ["date", '<input id="asof" type="date" value="2026-10-10">'],
    ["chart position", '<input id="chart-position" type="range" min="0" max="100" value="20">'],
    ["select", '<select id="residual-select"><option>sz.000678</option></select>'],
];

function focusedExternalControl(h, markup) {
    const holder = h.document.createElement("div");
    holder.innerHTML = markup;
    h.document.body.append(holder);
    const control = holder.firstElementChild;
    control.focus();
    assert.equal(h.document.activeElement, control);
    assert.equal(h.window.getSelection().toString(), "");
    return control;
}

function hovered(element) {
    const matches = element.matches.bind(element);
    element.matches = (selector) => (selector === ":hover" ? true : matches(selector));
}

for (const init of [
    { key: "c", ctrlKey: true },
    { key: "C", ctrlKey: true },
    { key: "c", ctrlKey: false, metaKey: true },
    { key: "C", ctrlKey: false, metaKey: true },
]) {
    test(`${init.metaKey ? "Meta" : "Ctrl"}+${init.key} copies without a native copy event or selection`, (t) => {
        const h = harness(t);
        assert.equal(h.window.getSelection().toString(), "");
        const event = h.key(h.canvas, init);
        assert.equal(event.defaultPrevented, true);
        assert.deepEqual(h.copied, [bar]);
        assert.deepEqual(h.formatted, []);
        assert.deepEqual(h.feedback, []);
        assert.deepEqual(h.nativeWrites, []);
    });
}

for (const key of ["с", "Process"]) {
    test(`physical KeyC copies the current candle with logical key ${key} and body keyboard focus`, (t) => {
        const h = harness(t);
        h.containerHovered = true;
        assert.equal(h.document.activeElement, h.document.body);
        assert.equal(h.key(h.document.activeElement, { key, code: "KeyC" }).defaultPrevented, true);
        assert.deepEqual(h.copied, [bar]);
    });
}

for (const init of [
    { key: "Process", code: "KeyX" },
    { key: "с", code: "KeyS" },
    { key: "Process", code: "KeyC", isComposing: true },
    { key: "Process", code: "KeyC", altKey: true },
    { key: "Process", code: "KeyC", shiftKey: true },
]) {
    test(`physical shortcut preserves native behavior for ${JSON.stringify(init)}`, (t) => {
        const h = harness(t);
        h.containerHovered = true;
        assertUntouched(h, h.key(h.document.activeElement, init));
    });
}

test("keyboard copying consumes repeats but invokes the asynchronous button path only once", async (t) => {
    const calls = [];
    let complete;
    const pending = new Promise((resolve) => {
        complete = resolve;
    });
    const h = harness(t, {
        copyCandle: (candle) => {
            calls.push(candle);
            return pending;
        },
    });
    assert.equal(h.key().defaultPrevented, true);
    for (let index = 0; index < 3; index += 1) assert.equal(h.key(h.canvas, { repeat: true }).defaultPrevented, true);
    assert.deepEqual(calls, [bar]);
    assert.deepEqual(h.feedback, []);
    complete();
    await pending;
    assert.deepEqual(h.feedback, []);
});

for (const [name, init] of [
    ["plain C", { ctrlKey: false }],
    ["a different shortcut", { key: "x" }],
    ["Alt", { altKey: true }],
    ["Shift", { shiftKey: true }],
    ["composition", { isComposing: true }],
]) {
    test(`does not take over ${name}`, (t) => {
        const h = harness(t);
        assertUntouched(h, h.key(h.canvas, init));
    });
}

test("an already prevented keyboard or native copy event is left to its owner", (t) => {
    const h = harness(t);
    const key = new h.window.KeyboardEvent("keydown", { key: "c", ctrlKey: true, bubbles: true, cancelable: true });
    key.preventDefault();
    h.canvas.dispatchEvent(key);
    h.copy(h.canvas, { prevented: true });
    assert.deepEqual(h.copied, []);
    assert.deepEqual(h.nativeWrites, []);
    assert.deepEqual(h.feedback, []);
});

for (const [name, arrange] of [
    [
        "chart hover",
        (h) => {
            h.containerHovered = true;
        },
    ],
    [
        "external tooltip hover",
        (h) => {
            h.tooltipHovered = true;
        },
    ],
    [
        "chart focus",
        (h) => {
            h.container.focus();
        },
    ],
    [
        "external tooltip button focus",
        (h) => {
            h.cardButton.focus();
        },
    ],
]) {
    test(`${name} supplies context when the event target is outside the chart`, (t) => {
        const h = harness(t);
        arrange(h);
        const event = h.key(h.outside);
        assert.equal(event.defaultPrevented, true);
        assert.deepEqual(h.copied, [bar]);
        assert.equal(h.copy(h.outside).defaultPrevented, true);
        assert.deepEqual(h.nativeWrites, [["text/plain", `snapshot:${bar.time}:${bar.close}`]]);
        assert.deepEqual(h.feedback, [bar]);
    });
}

test("an external tooltip target can copy while the chart itself is not hovered", (t) => {
    const h = harness(t);
    assert.equal(h.container.contains(h.tooltip), false);
    assert.equal(h.container.matches(":hover"), false);
    const event = h.copy(h.cardButton);
    assert.equal(event.defaultPrevented, true);
    assert.deepEqual(h.nativeWrites, [["text/plain", `snapshot:${bar.time}:${bar.close}`]]);
    assert.deepEqual(h.formatted, [bar]);
    assert.deepEqual(h.feedback, [bar]);
    assert.deepEqual(h.copied, []);
});

test("document capture receives keyboard and copy events despite SDK bubble suppression", (t) => {
    const h = harness(t);
    const sdkStates = [];
    for (const type of ["keydown", "copy"])
        h.canvas.addEventListener(type, (event) => {
            sdkStates.push([type, event.defaultPrevented]);
            event.stopPropagation();
        });
    h.key();
    h.copy();
    assert.deepEqual(sdkStates, [
        ["keydown", true],
        ["copy", true],
    ]);
    assert.deepEqual(h.copied, [bar]);
    assert.deepEqual(h.feedback, [bar]);
    assert.equal(h.nativeWrites.length, 1);
});

for (const [name, markup] of [
    ["input", "<input value='selected'>"],
    ["textarea", "<textarea>selected</textarea>"],
    ["select", "<select><option>selected</option></select>"],
    ["contenteditable", "<div contenteditable='true'><span>selected</span></div>"],
    ["textbox role", "<div role='textbox' tabindex='0'><span>selected</span></div>"],
]) {
    test(`${name} and its descendants retain ordinary text-copy behavior`, (t) => {
        const h = harness(t);
        h.containerHovered = true;
        const holder = h.document.createElement("div");
        holder.innerHTML = markup;
        h.document.body.append(holder);
        const target = holder.querySelector("span") ?? holder.firstElementChild;
        hovered(holder.firstElementChild);
        assertUntouched(h, h.key(target));
        assertUntouched(h, h.copy(target));
    });
}

for (const [name, markup] of externalControls) {
    for (const region of ["containerHovered", "tooltipHovered"]) {
        test(`${name} residual focus without a selection permits keyboard and native candle copying during ${region}`, (t) => {
            const h = harness(t);
            const control = focusedExternalControl(h, markup);
            h[region] = true;
            // 键盘事件仍派发给残留焦点的输入控件；真正鼠标范围才决定当前 K 线。
            assert.equal(h.key(control).defaultPrevented, true);
            assert.deepEqual(h.copied, [bar]);
            assert.equal(h.copy(control).defaultPrevented, true);
            assert.deepEqual(h.nativeWrites, [["text/plain", `snapshot:${bar.time}:${bar.close}`]]);
            assert.deepEqual(h.feedback, [bar]);
            assert.equal(h.document.activeElement, control);
        });
    }
    test(`${name} residual focus without actual chart hover keeps native copying even for a chart target`, (t) => {
        const h = harness(t);
        const control = focusedExternalControl(h, markup);
        assertUntouched(h, h.key(control));
        assertUntouched(h, h.copy(control));
        assertUntouched(h, h.key(h.canvas));
        assertUntouched(h, h.copy(h.canvas));
    });
}

for (const [name, markup] of [
    ["input", '<input value="selected text">'],
    ["textarea", "<textarea>selected text</textarea>"],
]) {
    test(`${name} input selection retains native copying when the pointer remains on its editor`, (t) => {
        const h = harness(t);
        const control = focusedExternalControl(h, markup);
        control.setSelectionRange(0, control.value.length);
        h.containerHovered = true;
        hovered(control);
        assert.equal(h.window.getSelection().toString(), "");
        assert.equal(control.selectionEnd - control.selectionStart, control.value.length);
        assertUntouched(h, h.key(control));
        assertUntouched(h, h.copy(control));
        assertUntouched(h, h.key(h.canvas));
        assertUntouched(h, h.copy(h.canvas));
        assert.equal(control.selectionEnd - control.selectionStart, control.value.length);
    });
}

for (const region of ["container", "tooltip"]) {
    test(`an editable control inside the ${region} keeps native copying without an input selection`, (t) => {
        const h = harness(t);
        const input = h.document.createElement("input");
        input.value = "unselected text";
        h[region].append(input);
        input.focus();
        input.setSelectionRange(input.value.length, input.value.length);
        h.containerHovered = true;
        assertUntouched(h, h.key(input));
        assertUntouched(h, h.copy(input));
        assertUntouched(h, h.key(h.canvas));
        assertUntouched(h, h.copy(h.canvas));
    });
}

test("a contenteditable text selection takes precedence over actual chart hover", (t) => {
    const h = harness(t);
    const editor = focusedExternalControl(h, '<div contenteditable="true" tabindex="0">selected text</div>');
    const range = h.document.createRange();
    range.selectNodeContents(editor);
    h.window.getSelection().removeAllRanges();
    h.window.getSelection().addRange(range);
    h.tooltipHovered = true;
    assert.equal(h.window.getSelection().toString(), "selected text");
    assertUntouched(h, h.key(editor));
    assertUntouched(h, h.copy(editor));
});

test("an editable field in a shadow root is recognized through the composed event path", (t) => {
    const h = harness(t);
    const host = h.document.createElement("div");
    h.container.append(host);
    const shadow = host.attachShadow({ mode: "open" });
    shadow.innerHTML = "<input>";
    const input = shadow.querySelector("input");
    assertUntouched(h, h.key(input));
    assertUntouched(h, h.copy(input));
});

test("noneditable tooltip content remains a valid candle-copy target", (t) => {
    const h = harness(t);
    const content = h.document.createElement("div");
    content.setAttribute("contenteditable", "false");
    h.tooltip.append(content);
    assert.equal(h.key(content).defaultPrevented, true);
    assert.deepEqual(h.copied, [bar]);
});

test("a selected tooltip field retains its native text-copy behavior", (t) => {
    const h = harness(t);
    h.tooltipHovered = true;
    const range = h.document.createRange();
    range.selectNodeContents(h.document.getElementById("details"));
    h.window.getSelection().addRange(range);
    assert.equal(h.window.getSelection().toString(), "日期与收盘价");
    assertUntouched(h, h.key(h.cardButton));
    assertUntouched(h, h.copy(h.cardButton));
});

for (const [name, arrange] of [
    ["outside chart context", () => {}],
    [
        "a hidden chart",
        (h) => {
            h.container.hidden = true;
        },
    ],
    [
        "a hidden chart ancestor",
        (h) => {
            h.container.parentElement.hidden = true;
        },
    ],
    [
        "an inert chart",
        (h) => {
            h.container.setAttribute("inert", "");
        },
    ],
    [
        "an inert chart ancestor",
        (h) => {
            h.container.parentElement.setAttribute("inert", "");
        },
    ],
    [
        "a hidden tooltip",
        (h) => {
            h.tooltip.hidden = true;
        },
    ],
    [
        "a hidden tooltip ancestor",
        (h) => {
            const ancestor = h.document.createElement("section");
            h.document.body.append(ancestor);
            ancestor.append(h.tooltip);
            ancestor.hidden = true;
        },
    ],
    [
        "an inert tooltip",
        (h) => {
            h.tooltip.setAttribute("inert", "");
        },
    ],
    [
        "a disconnected chart",
        (h) => {
            h.container.remove();
        },
    ],
    [
        "a disconnected tooltip",
        (h) => {
            h.tooltip.remove();
        },
    ],
    [
        "a disabled page",
        (h) => {
            h.enabled = false;
        },
    ],
    [
        "no current candle",
        (h) => {
            h.candle = null;
        },
    ],
]) {
    test(`does not copy with ${name}`, (t) => {
        const h = harness(t);
        if (name !== "outside chart context") h.containerHovered = true;
        arrange(h);
        assertUntouched(h, h.key(h.outside));
        assertUntouched(h, h.copy(h.outside));
    });
}

test("native copying without clipboardData is not claimed", (t) => {
    const h = harness(t);
    assertUntouched(h, h.copy(h.canvas, { clipboard: false }));
});

test("unbinding removes both captured handlers and remains safe to call twice", (t) => {
    const h = harness(t);
    h.unbind();
    h.unbind();
    assertUntouched(h, h.key());
    assertUntouched(h, h.copy());
});

function installApplicationCopy(
    h,
    {
        writeText = async (text) => {
            h.asyncWrites.push(text);
        },
    } = {},
) {
    const start = appSource.indexOf("function fallbackCopyText(text, focusTarget) {");
    const binding = appSource.indexOf("bindCandleClipboard(chart, {", start);
    const end = appSource.indexOf("\n});", binding);
    assert.ok(start >= 0 && binding > start && end > binding, "the production copy fragment must remain present");
    const source = appSource
        .slice(start, end + 4)
        .replace("bindCandleClipboard(chart, {", "boundCleanup = bindCandleClipboard(chart, {");
    const context = {
        document: h.document,
        window: h.window,
        navigator: { clipboard: { writeText } },
        chart: h.chart,
        currentCandle: bar,
        state: {
            page: "workspace",
            loading: false,
            error: null,
            view: { symbol: "sh.600000", data_source: "akshare", bars: [{ time: "2026-10-09", close: 10 }, bar] },
        },
        candleCopyFeedbackTimer: undefined,
        $: (id) => h.document.getElementById(id),
        symbolName: () => "测试股票",
        candleCopyText,
        previousCandleClose,
        bindCandleClipboard,
        activateHeaderStockSearch: () => {},
        requestAnimationFrame: (callback) => callback(),
        isMarketBrowse: () => ["akshare", "tdx"].includes(h.document.getElementById("result-scope").value),
        selectedTimeframe: () => h.document.getElementById("timeframe-select").dataset.value,
    };
    h.chart.data = context.state.view;
    // 执行应用真实的按钮、快捷键、格式化及反馈接线，只替代系统剪贴板 I/O。
    runInNewContext(source, context);
    const scopeStart = appSource.indexOf("const sourceForScope =");
    const scopeEnd = appSource.indexOf("\n", scopeStart);
    assert.ok(scopeStart >= 0 && scopeEnd > scopeStart, "the production source-scope fragment must remain present");
    runInNewContext(appSource.slice(scopeStart, scopeEnd), context);
    const timeframeStart = appSource.indexOf("const activeTimeframe =");
    const timeframeEnd = appSource.indexOf("\n", timeframeStart);
    assert.ok(
        timeframeStart >= 0 && timeframeEnd > timeframeStart,
        "the production active-timeframe fragment must remain present",
    );
    runInNewContext(appSource.slice(timeframeStart, timeframeEnd), context);
    const focusStart = appSource.indexOf("function focusStockSearch() {");
    const focusEnd = appSource.indexOf("\nwindow.addEventListener(", focusStart);
    assert.ok(focusStart >= 0 && focusEnd > focusStart, "the production search-focus fragment must remain present");
    runInNewContext(appSource.slice(focusStart, focusEnd), context);
    h.unbind = context.boundCleanup;
    return context;
}

test("application keyboard wiring writes the visible candle through the existing asynchronous button path", async (t) => {
    const h = harness(t, { bind: false });
    installApplicationCopy(h);
    assert.equal(h.key(h.cardButton).defaultPrevented, true);
    await setImmediate();
    assert.equal(h.asyncWrites.length, 1);
    assert.match(h.asyncWrites[0], /股票：测试股票（sh\.600000）/);
    assert.match(h.asyncWrites[0], /日期：2026-10-10/);
    assert.match(h.asyncWrites[0], /涨跌幅：\+10\.00%/);
    assert.equal(h.topButton.textContent, "已复制");
    assert.equal(h.document.getElementById("candle-copy-feedback").textContent, "已复制 2026-10-10 K 线数据");
    assert.deepEqual(h.nativeWrites, []);
});

for (const region of ["containerHovered", "tooltipHovered"]) {
    for (const selectionEcho of ["none", "no DOM range", "collapsed body range"]) {
        test(`real search focus and select preserves its residual selection while ${region} copies with ${selectionEcho}`, async (t) => {
            const h = harness(t, { bind: false });
            const context = installApplicationCopy(h);
            const control = focusedExternalControl(
                h,
                '<input id="header-stock-search" type="search" value="襄阳轴承">',
            );
            context.focusStockSearch();
            assert.equal(h.document.activeElement, control);
            assert.deepEqual([control.selectionStart, control.selectionEnd], [0, control.value.length]);
            if (selectionEcho !== "none") {
                const range = h.document.createRange();
                range.selectNodeContents(h.document.body);
                range.collapse(true);
                // 浏览器可能回显输入选区文字，但它不是选中了卡片或正文的 DOM Range。
                h.document.getSelection = () => ({
                    toString: () => control.value,
                    isCollapsed: false,
                    anchorNode: h.document.body,
                    focusNode: h.document.body,
                    anchorOffset: 0,
                    focusOffset: 0,
                    rangeCount: selectionEcho === "no DOM range" ? 0 : 1,
                    getRangeAt: () => range,
                });
            }
            h[region] = true;
            assert.equal(h.key(h.document.activeElement).defaultPrevented, true);
            await setImmediate();
            const expected = candleCopyText(bar, "测试股票（sh.600000）", 10);
            assert.deepEqual(h.asyncWrites, [expected]);
            assert.equal(h.topButton.dataset.copyState, "success");
            assert.equal(h.copy(h.document.activeElement).defaultPrevented, true);
            assert.deepEqual(h.nativeWrites, [["text/plain", expected]]);
            assert.equal(h.document.activeElement, control);
            assert.deepEqual([control.selectionStart, control.selectionEnd], [0, control.value.length]);
            h[region] = false;
            assert.equal(h.key(control).defaultPrevented, false);
            assert.equal(h.copy(control).defaultPrevented, false);
            assert.deepEqual(h.asyncWrites, [expected]);
            assert.deepEqual(h.nativeWrites, [["text/plain", expected]]);
        });
    }
}

for (const status of ["loading", "error"]) {
    test(`application copies the same visible stock during background ${status} through all copy entries`, async (t) => {
        const h = harness(t, { bind: false });
        const context = installApplicationCopy(h);
        context.state[status] = status === "error" ? "background backtest failed" : true;
        h.document.getElementById("result-scope").value = "akshare-backtest";
        assert.equal(h.chart.data, context.state.view);
        h.containerHovered = true;
        assert.equal(h.key(h.document.activeElement).defaultPrevented, true);
        await setImmediate();
        const expected = candleCopyText(bar, "测试股票（sh.600000）", 10);
        assert.deepEqual(h.asyncWrites, [expected]);
        assert.equal(h.copy(h.document).defaultPrevented, true);
        assert.deepEqual(h.nativeWrites, [["text/plain", expected]]);
        h.topButton.click();
        await setImmediate();
        assert.deepEqual(h.asyncWrites, [expected, expected]);
        assert.equal(h.topButton.dataset.copyState, "success");
    });
}

test("requested AkShare data resolved through TDX remains copyable as the displayed logical source", async (t) => {
    const h = harness(t, { bind: false });
    const context = installApplicationCopy(h);
    context.state.view.resolved_source = "tdx";
    assert.equal(context.state.view.data_source, "akshare");
    h.containerHovered = true;
    assert.equal(h.key(h.document.activeElement).defaultPrevented, true);
    await setImmediate();
    assert.deepEqual(h.asyncWrites, [candleCopyText(bar, "测试股票（sh.600000）", 10)]);
});

for (const [name, markup] of externalControls) {
    for (const region of ["containerHovered", "tooltipHovered"]) {
        test(`application wiring copies the visible candle with ${name} residual focus during ${region}`, async (t) => {
            const h = harness(t, { bind: false });
            installApplicationCopy(h);
            const control = focusedExternalControl(h, markup);
            h[region] = true;
            assert.equal(h.key(control).defaultPrevented, true);
            await setImmediate();
            const expected = candleCopyText(bar, "测试股票（sh.600000）", 10);
            assert.deepEqual(h.asyncWrites, [expected]);
            assert.equal(h.topButton.textContent, "已复制");
            assert.equal(h.topButton.dataset.copyState, "success");
            assert.equal(h.document.getElementById("candle-copy-feedback").textContent, "已复制 2026-10-10 K 线数据");
            assert.deepEqual(h.nativeWrites, []);
            assert.equal(h.copy(control).defaultPrevented, true);
            assert.deepEqual(h.nativeWrites, [["text/plain", expected]]);
            assert.deepEqual(h.asyncWrites, [expected]);
            assert.equal(h.document.activeElement, control);
        });
    }
}

test("application copying with stock-search residual focus awaits the asynchronous write and consumes repeats", async (t) => {
    const h = harness(t, { bind: false });
    let finish;
    const pending = new Promise((resolve) => {
        finish = resolve;
    });
    installApplicationCopy(h, {
        writeText: async (text) => {
            h.asyncWrites.push(text);
            await pending;
        },
    });
    const control = focusedExternalControl(h, externalControls[0][1]);
    h.containerHovered = true;
    assert.equal(h.key(control).defaultPrevented, true);
    assert.equal(h.key(control, { repeat: true }).defaultPrevented, true);
    assert.deepEqual(h.asyncWrites, [candleCopyText(bar, "测试股票（sh.600000）", 10)]);
    assert.equal(h.topButton.textContent, "复制 K 线");
    assert.equal(h.topButton.dataset.copyState, undefined);
    finish();
    await setImmediate();
    assert.equal(h.asyncWrites.length, 1);
    assert.equal(h.topButton.dataset.copyState, "success");
    assert.equal(h.document.getElementById("candle-copy-feedback").textContent, "已复制 2026-10-10 K 线数据");
    assert.deepEqual(h.nativeWrites, []);
});

test("application native copying formats synchronously without invoking the asynchronous clipboard API", (t) => {
    const h = harness(t, { bind: false });
    installApplicationCopy(h);
    assert.equal(h.copy(h.cardButton).defaultPrevented, true);
    assert.equal(h.nativeWrites.length, 1);
    assert.equal(h.nativeWrites[0][0], "text/plain");
    assert.match(h.nativeWrites[0][1], /日期：2026-10-10/);
    assert.deepEqual(h.asyncWrites, []);
    assert.equal(h.topButton.textContent, "已复制");
});

for (const [name, arrange] of [
    [
        "another page",
        (h, context) => {
            context.state.page = "health";
        },
    ],
    [
        "a replaced chart snapshot",
        (h, context) => {
            h.chart.data = { ...context.state.view };
        },
    ],
    [
        "a changed stock selector",
        (h) => {
            h.document.getElementById("symbol-select").value = "sz.000678";
        },
    ],
    [
        "a visible chart loading overlay",
        (h) => {
            h.document.getElementById("chart-loading-overlay").hidden = false;
        },
    ],
    [
        "an aria-busy chart",
        (h) => {
            h.container.setAttribute("aria-busy", "true");
        },
    ],
    [
        "a candle absent from the rendered bars despite matching date and prices",
        (h, context) => {
            context.currentCandle = { ...bar };
        },
    ],
    [
        "no rendered chart data",
        (h) => {
            h.chart.data = null;
        },
    ],
    [
        "a changed market source for the same stock",
        (h) => {
            h.document.getElementById("result-scope").value = "tdx";
        },
    ],
    [
        "a changed timeframe for the same stock",
        (h) => {
            h.document.getElementById("timeframe-select").dataset.value = "1w";
        },
    ],
    [
        "TradingView",
        (h) => {
            h.container.parentElement.dataset.chartView = "tradingview";
        },
    ],
    [
        "an old candle date",
        (h) => {
            h.chart.tooltipBarTime = "2026-10-09";
        },
    ],
    [
        "no current candle",
        (h, context) => {
            context.currentCandle = null;
        },
    ],
]) {
    test(`application wiring ignores ${name} for keyboard and native copying`, async (t) => {
        const h = harness(t, { bind: false });
        const context = installApplicationCopy(h);
        arrange(h, context);
        assert.equal(h.key(h.cardButton).defaultPrevented, false);
        assert.equal(h.copy(h.cardButton).defaultPrevented, false);
        await setImmediate();
        assert.deepEqual(h.asyncWrites, []);
        assert.deepEqual(h.nativeWrites, []);
        assert.equal(h.topButton.textContent, "复制 K 线");
        if (name !== "an old candle date") {
            h.topButton.click();
            await setImmediate();
            assert.deepEqual(h.asyncWrites, []);
            assert.equal(h.topButton.dataset.copyState, undefined);
        }
    });
}

test("the application fallback focuses and selects its textarea without reentering candle-copy capture", async (t) => {
    const h = harness(t, { bind: false });
    h.containerHovered = true;
    const control = focusedExternalControl(h, externalControls[0][1]);
    const fallbackValues = [];
    h.document.execCommand = (command) => {
        assert.equal(command, "copy");
        const field = h.document.activeElement;
        assert.equal(field.tagName, "TEXTAREA");
        assert.equal(field.selectionStart, 0);
        assert.equal(field.selectionEnd, field.value.length);
        fallbackValues.push(field.value);
        assert.equal(h.copy(field).defaultPrevented, false);
        assert.equal(h.copy(h.document).defaultPrevented, false);
        assert.equal(field.dataset.clipboardFallback, "true");
        return true;
    };
    installApplicationCopy(h, {
        writeText: async () => {
            throw new Error("permission denied");
        },
    });
    assert.equal(h.key(control).defaultPrevented, true);
    await setImmediate();
    assert.equal(fallbackValues.length, 1);
    assert.match(fallbackValues[0], /日期：2026-10-10/);
    assert.deepEqual(h.nativeWrites, []);
    assert.equal(h.document.querySelector("textarea"), null);
    assert.equal(h.document.activeElement, h.topButton);
    assert.equal(h.topButton.textContent, "已复制");
});

test("a marked application fallback for other text cannot be replaced by candle copying even without an input selection", async (t) => {
    const h = harness(t, { bind: false });
    h.containerHovered = true;
    const fallbackValues = [];
    h.document.execCommand = (command) => {
        assert.equal(command, "copy");
        const field = h.document.activeElement;
        assert.equal(field.tagName, "TEXTAREA");
        field.setSelectionRange(field.value.length, field.value.length);
        assert.equal(h.copy(h.document).defaultPrevented, false);
        assert.equal(h.copy(field).defaultPrevented, false);
        assert.equal(field.dataset.clipboardFallback, "true");
        fallbackValues.push(field.value);
        return true;
    };
    const context = installApplicationCopy(h, {
        writeText: async () => {
            throw new Error("permission denied");
        },
    });
    const text = "股票：襄阳轴承（sz.000678）";
    assert.equal(await context.writeClipboardText(text, h.topButton), true);
    assert.deepEqual(fallbackValues, [text]);
    assert.deepEqual(h.nativeWrites, []);
    assert.equal(h.topButton.textContent, "复制 K 线");
    assert.equal(h.topButton.dataset.copyState, undefined);
    assert.equal(h.document.querySelector("textarea"), null);
    assert.equal(h.document.activeElement, h.topButton);
});

test("the application reports failure when both clipboard permission and the fallback fail", async (t) => {
    const h = harness(t, { bind: false });
    h.containerHovered = true;
    let fallbackCalls = 0;
    h.document.execCommand = (command) => {
        assert.equal(command, "copy");
        const field = h.document.activeElement;
        assert.equal(field.tagName, "TEXTAREA");
        assert.equal(h.copy(field).defaultPrevented, false);
        fallbackCalls += 1;
        return false;
    };
    installApplicationCopy(h, {
        writeText: async () => {
            throw new Error("permission denied");
        },
    });
    assert.equal(h.key(h.cardButton).defaultPrevented, true);
    await setImmediate();
    assert.equal(fallbackCalls, 1);
    assert.deepEqual(h.nativeWrites, []);
    assert.equal(h.document.querySelector("textarea"), null);
    assert.equal(h.document.activeElement, h.topButton);
    assert.equal(h.topButton.textContent, "复制失败");
    assert.equal(h.topButton.dataset.copyState, "error");
    assert.equal(h.document.getElementById("candle-copy-feedback").textContent, "复制失败，请检查剪贴板权限");
});

test("the application falls back successfully when the clipboard API is absent", async (t) => {
    const h = harness(t, { bind: false });
    const fallbackValues = [];
    h.document.execCommand = (command) => {
        assert.equal(command, "copy");
        const field = h.document.activeElement;
        assert.equal(field.tagName, "TEXTAREA");
        assert.equal(field.selectionStart, 0);
        assert.equal(field.selectionEnd, field.value.length);
        fallbackValues.push(field.value);
        assert.equal(h.copy(field).defaultPrevented, false);
        return true;
    };
    const context = installApplicationCopy(h);
    delete context.navigator.clipboard;
    assert.equal(h.key(h.cardButton).defaultPrevented, true);
    await setImmediate();
    assert.equal(fallbackValues.length, 1);
    assert.match(fallbackValues[0], /日期：2026-10-10/);
    assert.deepEqual(h.asyncWrites, []);
    assert.deepEqual(h.nativeWrites, []);
    assert.equal(h.document.querySelector("textarea"), null);
    assert.equal(h.document.activeElement, h.topButton);
    assert.equal(h.topButton.textContent, "已复制");
    assert.equal(h.topButton.dataset.copyState, "success");
    assert.equal(h.document.getElementById("candle-copy-feedback").textContent, "已复制 2026-10-10 K 线数据");
});
