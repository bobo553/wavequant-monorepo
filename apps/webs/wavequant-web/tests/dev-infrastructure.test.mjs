import assert from "node:assert/strict";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import {
    isInfrastructureConfigured,
    loadEnvironmentDefaults,
    resolveStructureWorkerCount,
    shouldStartBundledInfrastructure,
} from "../scripts/dev-infrastructure.mjs";

test("development infrastructure env supplies defaults without overriding the shell", () => {
    const directory = mkdtempSync(join(tmpdir(), "wavequant-dev-env-"));
    const path = join(directory, ".env.infrastructure");
    writeFileSync(
        path,
        "WAVEQUANT_DATABASE_URL=mysql+pymysql://file-value@127.0.0.1/wavequant\nWAVEQUANT_REDIS_URL=redis://127.0.0.1:6380/0\n",
    );
    const environment = { WAVEQUANT_DATABASE_URL: "sqlite+pysqlite:///:memory:" };
    try {
        assert.deepEqual(loadEnvironmentDefaults(path, environment).sort(), [
            "WAVEQUANT_DATABASE_URL",
            "WAVEQUANT_REDIS_URL",
        ]);
        assert.equal(environment.WAVEQUANT_DATABASE_URL, "sqlite+pysqlite:///:memory:");
        assert.equal(environment.WAVEQUANT_REDIS_URL, "redis://127.0.0.1:6380/0");
    } finally {
        rmSync(directory, { force: true, recursive: true });
    }
});

test("bundled compose starts only for local MySQL and Redis", () => {
    assert.equal(
        shouldStartBundledInfrastructure({
            WAVEQUANT_DATABASE_URL: "mysql+pymysql://wavequant:secret@127.0.0.1:3306/wavequant",
            WAVEQUANT_REDIS_URL: "redis://localhost:6380/0",
        }),
        true,
    );
    assert.equal(
        shouldStartBundledInfrastructure({
            WAVEQUANT_DATABASE_URL: "postgresql+psycopg://wavequant:secret@db.example/wavequant",
            WAVEQUANT_REDIS_URL: "redis://cache.example:6379/0",
        }),
        false,
    );
    assert.equal(
        shouldStartBundledInfrastructure({
            WAVEQUANT_DATABASE_URL: "mysql+pymysql://wavequant:secret@127.0.0.1:3306/wavequant",
            WAVEQUANT_REDIS_URL: "redis://localhost:6380/0",
            WAVEQUANT_DEV_AUTO_INFRA: "false",
        }),
        false,
    );
});

test("structure read models are enabled only when a database URL is present", () => {
    assert.equal(isInfrastructureConfigured({}), false);
    assert.equal(isInfrastructureConfigured({ WAVEQUANT_DATABASE_URL: "sqlite+pysqlite:///:memory:" }), true);
});

test("AkShare structure workers default to two foreground-safe bounded shards", () => {
    assert.equal(resolveStructureWorkerCount({}), 2);
    assert.equal(resolveStructureWorkerCount({ WAVEQUANT_DEV_STRUCTURE_WORKERS: "4" }), 4);
    assert.equal(resolveStructureWorkerCount({ WAVEQUANT_DEV_STRUCTURE_WORKERS: "off" }), 0);
    assert.throws(() => resolveStructureWorkerCount({ WAVEQUANT_DEV_STRUCTURE_WORKERS: "17" }), /must be 1-16/);
});
