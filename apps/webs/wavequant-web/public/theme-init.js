(() => {
    try {
        const raw = localStorage.getItem("wavequant.market.v2");
        const saved = raw ? JSON.parse(raw)?.state : null;
        if (!saved) return;
        const mode = saved.theme === "light" ? "light" : "dark";
        document.documentElement.classList.toggle("light", mode === "light");
        document.documentElement.classList.toggle("dark", mode === "dark");
        document.documentElement.dataset.theme = saved.colorTheme === "market-blue" ? "market-blue" : "wavequant-teal";
        document.documentElement.dataset.palette = saved.palette === "accessible" ? "accessible" : "classic";
        document.documentElement.dataset.density = saved.density === "compact" ? "compact" : "comfortable";
    } catch {
        // Corrupt or blocked storage falls back to server-rendered defaults.
    }
})();
