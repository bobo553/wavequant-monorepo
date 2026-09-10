import { render, screen } from "@testing-library/react";

import { MetricCard } from "@/features/overview/components/metric-card";

describe("MetricCard", () => {
    it("renders the metric value and comparison", () => {
        render(<MetricCard label="本月营收" value="¥ 286,400" change="+12.8%" direction="up" detail="较上月增加" />);

        expect(screen.getByText("本月营收")).toBeInTheDocument();
        expect(screen.getByText("¥ 286,400")).toBeInTheDocument();
        expect(screen.getByText("+12.8%")).toBeInTheDocument();
    });
});
