import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ResearchControls } from "../src/features/research-workbench/components/research-controls";

describe("ResearchControls", () => {
    it("starts with the optional backtest volume filter disabled", () => {
        render(<ResearchControls />);

        expect(screen.getByRole("checkbox", { name: /启用量能过滤/ })).not.toBeChecked();
        expect(screen.getByRole("checkbox", { name: /启用成交价含费净盈亏比过滤/ })).not.toBeChecked();
    });
});
