const origins = new WeakMap();
/** 节点表保持为唯一数据源；字段按需读取，同一节点只建立一个只读视图。 */
export function decodeIndexedDocument(document) {
    if (!document || typeof document !== "object" || document.$format !== "wavequant.indexed.v1") return document;
    const { nodes, root } = document;
    if (!Array.isArray(nodes) || nodes.length > 1_000_000 || !Number.isSafeInteger(root) || root < 0 || root >= nodes.length) {
        throw new Error("索引数据格式无效");
    }
    const depths = new Uint16Array(nodes.length);
    let edges = 0;
    const checkReference = (id, owner) => {
        if (!Number.isSafeInteger(id) || id < 0 || id >= owner) throw new Error("索引数据引用越界");
        depths[owner] = Math.max(depths[owner], depths[id] + 1);
        if (depths[owner] > 256 || ++edges > 4_000_000) throw new Error("索引数据超过读取范围");
    };
    for (let i = 0; i < nodes.length; i++) {
        const row = nodes[i];
        if (!Array.isArray(row) || row.length !== 2) throw new Error("索引节点格式无效");
        const [kind, value] = row;
        if (kind === 0 && value === null) continue;
        if (kind === 1 && typeof value === "boolean") continue;
        if (kind === 2 && typeof value === "number" && Number.isFinite(value)) continue;
        if (kind === 3 && typeof value === "string") continue;
        if (kind === 4 && Array.isArray(value)) { for (const id of value) checkReference(id, i); continue; }
        if (kind === 5 && Array.isArray(value)) {
            const names = new Set();
            for (const pair of value) {
                if (!Array.isArray(pair) || pair.length !== 2 || typeof pair[0] !== "string" || names.has(pair[0])) throw new Error("索引字段格式无效");
                names.add(pair[0]); checkReference(pair[1], i);
            }
            continue;
        }
        throw new Error("索引节点类型无效");
    }
    const views = new Map();
    const read = (id) => {
        if (views.has(id)) return views.get(id);
        const [kind, payload] = nodes[id];
        if (kind < 4) return payload;
        const value = kind === 4 ? new Array(payload.length) : Object.create(null);
        views.set(id, value);
        origins.set(value, { nodes, root: id });
        const fields = kind === 4 ? payload.map((ref, i) => [i, ref]) : payload;
        for (const [key, ref] of fields) Object.defineProperty(value, key, { enumerable: true, get: () => read(ref) });
        return Object.freeze(value);
    };
    return read(root);
}

/** 浏览器缓存保留节点表，不把读取视图展开为重复对象。 */
export function encodeIndexedDocument(value) {
    if (value && typeof value === "object" && origins.has(value)) return { $format: "wavequant.indexed.v1", ...origins.get(value) };
    const nodes = [], objects = new WeakMap(), visiting = new WeakSet(), intern = new Map();
    const visit = (item, depth) => {
        if (depth > 256) throw new Error("索引缓存超过范围");
        if (item && typeof item === "object" && objects.has(item)) return objects.get(item);
        if (item && typeof item === "object" && visiting.has(item)) throw new Error("索引缓存不能引用自身");
        let row;
        if (item === null) row = [0, null];
        else if (typeof item === "boolean") row = [1, item];
        else if (typeof item === "number" && Number.isFinite(item)) row = [2, item];
        else if (typeof item === "string") row = [3, item];
        else if (item && typeof item === "object") {
            visiting.add(item);
            row = Array.isArray(item) ? [4, item.map((v) => visit(v ?? null, depth + 1))]
                : [5, Object.keys(item).sort().filter((k) => item[k] !== undefined).map((k) => [k, visit(item[k], depth + 1)])];
            visiting.delete(item);
        } else throw new Error("索引缓存包含非数据字段");
        const key = JSON.stringify(row);
        let id = intern.get(key);
        if (id === undefined) {
            if (nodes.length >= 1_000_000) throw new Error("索引缓存超过范围");
            id = nodes.length; nodes.push(row); intern.set(key, id);
        }
        if (item && typeof item === "object") objects.set(item, id);
        return id;
    };
    return { $format: "wavequant.indexed.v1", root: visit(value, 0), nodes };
}
