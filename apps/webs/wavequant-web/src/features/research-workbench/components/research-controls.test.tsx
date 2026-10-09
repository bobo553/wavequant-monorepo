import { fireEvent, render, screen } from "@testing-library/react";

import { ResearchControls } from "./research-controls";

describe("ResearchControls N target trend option", () => {
    it("starts unchecked and remains selectable for every strategy", () => {
        render(<ResearchControls />);
        const checkbox = screen.getByRole("checkbox", { name: "正/倒 N 超一饱确认趋势" });
        expect(checkbox).not.toBeChecked();
        const variants = screen.getByRole("combobox", { name: "策略版本" });
        for (const variant of ["lecture_v3", "lecture_v2", "lecture_v1", "strict_full", "proxy_full"]) {
            fireEvent.change(variants, { target: { value: variant } });
            expect(checkbox).toBeEnabled();
        }
        fireEvent.click(checkbox);
        expect(checkbox).toBeChecked();
        fireEvent.click(checkbox);
        expect(checkbox).not.toBeChecked();
    });
});
