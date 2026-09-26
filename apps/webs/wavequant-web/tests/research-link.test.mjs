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
