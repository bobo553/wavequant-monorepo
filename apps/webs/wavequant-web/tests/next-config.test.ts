import { PHASE_DEVELOPMENT_SERVER, PHASE_PRODUCTION_BUILD } from "next/constants";

import { describe, expect, it } from "vitest";

import configureNext from "../next.config";

describe("Next.js API rewrite", () => {
    it("keeps the development proxy open for the full backtest request window", () => {
        const config = configureNext(PHASE_DEVELOPMENT_SERVER);

        expect(config.experimental?.proxyTimeout).toBeGreaterThan(300_000);
    });

    it("keeps the production static export free of development proxy settings", () => {
        const config = configureNext(PHASE_PRODUCTION_BUILD);

        expect(config.output).toBe("export");
        expect(config.experimental?.proxyTimeout).toBeUndefined();
    });
});
