import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ResearchControls } from "../src/features/research-workbench/components/research-controls";

describe("ResearchControls", () => {
    it("defaults buy intervals to level two and offers all three levels", () => {
        render(<ResearchControls />);
        const selector = screen.getByRole("combobox", { name: "买点筛选级别" });
        expect(selector).toHaveValue("2");
        expect(
            within(selector)
                .getAllByRole("option")
                .map((option) => option.getAttribute("value")),
        ).toEqual(["1", "2", "3"]);
    });
    it("starts with the optional backtest volume filter disabled", () => {
        render(<ResearchControls />);

        expect(screen.getByRole("checkbox", { name: /启用量能过滤/ })).not.toBeChecked();
        expect(screen.getByRole("checkbox", { name: /启用成交价含费净盈亏比过滤/ })).not.toBeChecked();
    });
});
