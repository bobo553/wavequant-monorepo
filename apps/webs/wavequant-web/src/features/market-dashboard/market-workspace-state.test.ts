import { defaultWorkspaceState, restoreWorkspaceState } from "@/features/market-dashboard/market-workspace-state";

describe("market workspace theme state", () => {
    it("restores the finance theme from the existing versioned workspace payload", () => {
        const restored = restoreWorkspaceState({
            version: 2,
            state: { colorTheme: "market-blue", theme: "light" },
        });

        expect(restored.colorTheme).toBe("market-blue");
        expect(restored.theme).toBe("light");
    });

    it("falls back to the WaveQuant theme for unknown or legacy values", () => {
        expect(restoreWorkspaceState({ version: 2, state: { colorTheme: "red" } }).colorTheme).toBe("wavequant-teal");
        expect(restoreWorkspaceState(null)).toBe(defaultWorkspaceState);
    });
});
