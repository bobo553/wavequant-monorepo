import "@testing-library/jest-dom/vitest";

Element.prototype.scrollIntoView = vi.fn();
global.ResizeObserver = class ResizeObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
};
