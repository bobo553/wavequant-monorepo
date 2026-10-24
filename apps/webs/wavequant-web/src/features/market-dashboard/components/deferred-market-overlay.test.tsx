import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MarketWorkspaceProvider, useMarketWorkspace } from "../market-workspace-context";
import { DeferredMarketOverlay } from "./deferred-market-overlay";

function Actions() {
    const { openSettings, saveNote } = useMarketWorkspace();
    return <><button onClick={openSettings}>打开设置</button><button onClick={saveNote}>保存笔记</button></>;
}
describe("DeferredMarketOverlay", () => {
    it("keeps lightweight feedback independent and allows closing before a dialog body loads", async () => {
        render(<MarketWorkspaceProvider><Actions /><DeferredMarketOverlay /></MarketWorkspaceProvider>);
        fireEvent.click(screen.getByRole("button", { name: "保存笔记" }));
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", { name: "打开设置" }));
        expect(screen.getByRole("dialog")).toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", { name: "关闭对话框" }));
        await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
        fireEvent.click(screen.getByRole("button", { name: "打开设置" }));
        expect(await screen.findByLabelText("主题")).toBeInTheDocument();
        fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
});
