import assert from "node:assert/strict";
import test from "node:test";

import { parseResearchLink, resolveResearchLink } from "../public/research-link.js";

test("stock navigation preserves the exact symbol, historical date and preferred source", () => {
    for (const symbol of ["sz.001216", "sh.600519", "bj.920001"]) {
        assert.deepEqual(parseResearchLink(`?symbol=${symbol}&asof=2026-09-18&source=akshare`), {
            symbol,
            asof: "2026-09-18",
            source: "akshare",
        });
    }
    assert.equal(parseResearchLink("?page=workspace"), null);
});
test("指数研究链接保留交易所身份，仅开放已实现的AkShare指数数据通道", () => {
    const index=parseResearchLink("?symbol=sh.000001&source=akshare");
    assert.equal(index.symbol,"sh.000001");
    assert.equal(parseResearchLink("?symbol=sz.000001").symbol,"sz.000001");
    assert.throws(()=>parseResearchLink("?symbol=sh.000001&source=tdx"),/指数行情/);
    const catalog={with_daily:1,stocks:[{symbol:"sh.000001",has_data:true}]};
    assert.equal(resolveResearchLink(index,{akshare:catalog}).symbol,"sh.000001");
});

test("invalid dates, symbols, repeated parameters and unsupported sources cannot trigger navigation", () => {
    for (const search of [
        "?symbol=invalid",
        "?symbol=sz.600519",
        "?symbol=sz.001216&asof=2026-02-30",
        "?symbol=sz.001216&asof=",
        "?symbol=sz.001216&source=unknown",
        "?symbol=sz.001216&symbol=sh.600519",
    ])
        assert.throws(() => parseResearchLink(search));
});

test("stock links resolve only against their selected data source", () => {
    const link = parseResearchLink("?symbol=sz.001216&asof=2026-09-18");
    const available = { with_daily: 1, stocks: [{ symbol: "sz.001216", has_data: true }] };
    assert.deepEqual(resolveResearchLink(link, { akshare: available, tdx: available }), { ...link, fallback: false });
    assert.throws(
        () => resolveResearchLink(link, { akshare: { with_daily: 0 }, tdx: available }),
        /所选AkShare目录中暂无可用行情/,
    );
    const tdxLink = { ...link, source: "tdx" };
    assert.deepEqual(resolveResearchLink(tdxLink, { akshare: available, tdx: available }), {
        ...tdxLink,
        fallback: false,
    });
    assert.throws(
        () => resolveResearchLink(tdxLink, { akshare: available, tdx: { with_daily: 0 } }),
        /所选通达信目录中暂无可用行情/,
    );
    assert.throws(
        () => resolveResearchLink(link, { akshare: { with_daily: 1, stocks: [{ symbol: "sh.600519" }] } }),
        /sz.001216/,
    );
    assert.throws(() =>
        resolveResearchLink(link, { akshare: { with_daily: 1, stocks: [{ symbol: "sz.001216", has_data: false }] } }),
    );
    assert.throws(() => resolveResearchLink({ ...link, source: "other" }, { akshare: available }), /数据源无效/);
});
