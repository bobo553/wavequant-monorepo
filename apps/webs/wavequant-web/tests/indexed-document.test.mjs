import assert from "node:assert/strict";
import test from "node:test";
import { decodeIndexedDocument, encodeIndexedDocument } from "../public/indexed-document.js";

test("repeated point and proof references share one immutable view", () => {
    const original = { value: 12, known: 3 };
    const document = encodeIndexedDocument({ key: original, anchor: original, points: [original, original] });
    const decoded = decodeIndexedDocument(document);
    assert.equal(decoded.key, decoded.anchor);
    assert.equal(decoded.key, decoded.points[0]);
    assert.equal(decoded.points[0], decoded.points[1]);
    assert.equal(decoded.key.value, 12);
    assert.throws(() => { decoded.key.value = 0; }, TypeError);
    assert.equal(encodeIndexedDocument(decoded).nodes, document.nodes);
});
test("bad references fail before a chart consumes data", () => {
    for (const document of [
        { $format: "wavequant.indexed.v1", root: 0, nodes: [[4, [0]]] },
        { $format: "wavequant.indexed.v1", root: 1, nodes: [[0, null]] },
        { $format: "wavequant.indexed.v1", root: 0, nodes: [[5, [["x", 0]]]] },
    ]) assert.throws(() => decodeIndexedDocument(document));
});
test("old cached JSON remains readable and field names cannot mutate prototypes", () => {
    const old = { bars: [] }; assert.equal(decodeIndexedDocument(old), old);
    const decoded = decodeIndexedDocument({ $format: "wavequant.indexed.v1", root: 1, nodes: [[3, "safe"], [5, [["__proto__", 0]]]] });
    assert.equal(Object.getPrototypeOf(decoded), null);
    assert.equal(decoded.__proto__, "safe");
});
