import { readFileSync } from "node:fs";
import process from "node:process";
import { parseEnv } from "node:util";

const loopbackHosts = new Set(["127.0.0.1", "localhost", "::1", "[::1]"]);
const disabledValues = new Set(["0", "false", "no", "off"]);

/** Load local infrastructure defaults without overriding explicit shell configuration. */
export function loadEnvironmentDefaults(path, environment = process.env) {
    const values = parseEnv(readFileSync(path, "utf8"));
    for (const [key, value] of Object.entries(values)) {
        if (environment[key] === undefined) environment[key] = value;
    }
    return Object.keys(values);
}

function isLoopbackUrl(value, protocols) {
    if (!value) return false;
    try {
        const parsed = new URL(value);
        return protocols.includes(parsed.protocol) && loopbackHosts.has(parsed.hostname);
    } catch {
        return false;
    }
}

/** The bundled compose stack is only appropriate for the local MySQL/Redis development URLs. */
export function shouldStartBundledInfrastructure(environment = process.env) {
    if (disabledValues.has(String(environment.WAVEQUANT_DEV_AUTO_INFRA || "").toLowerCase())) return false;
    return (
        isLoopbackUrl(environment.WAVEQUANT_DATABASE_URL, ["mysql+pymysql:"]) &&
        isLoopbackUrl(environment.WAVEQUANT_REDIS_URL, ["redis:", "rediss:"])
    );
}

export function isInfrastructureConfigured(environment = process.env) {
    return Boolean(environment.WAVEQUANT_DATABASE_URL);
}

/** Keep the full-catalog AkShare refresh bounded while allowing local overrides. */
export function resolveStructureWorkerCount(environment = process.env) {
    if (disabledValues.has(String(environment.WAVEQUANT_DEV_STRUCTURE_WORKERS || "").toLowerCase())) return 0;
    // 扫描按需开启后默认仅用一个分片，为网页/API 留出 CPU 和内存。
    const value = Number(environment.WAVEQUANT_DEV_STRUCTURE_WORKERS || 1);
    if (!Number.isInteger(value) || value < 1 || value > 16) {
        throw new Error("WAVEQUANT_DEV_STRUCTURE_WORKERS must be 1-16 or false");
    }
    return value;
}

/** 日常开发不扫描；显式选择数据源后才创建对应的后台进程。 */
export function resolveSignalWorkerPlan(mode = "off", environment = process.env) {
    const normalized = String(mode).trim().toLowerCase();
    if (disabledValues.has(normalized)) return { tdx: false, akshareShards: 0 };
    if (!["akshare", "tdx", "all"].includes(normalized)) {
        throw new Error("--signals must be off, akshare, tdx or all");
    }
    return {
        tdx: normalized === "tdx" || normalized === "all",
        akshareShards: normalized === "akshare" || normalized === "all" ? resolveStructureWorkerCount(environment) : 0,
    };
}
