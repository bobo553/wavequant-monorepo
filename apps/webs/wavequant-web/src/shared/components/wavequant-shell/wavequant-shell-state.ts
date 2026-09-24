"use client";

import { useSyncExternalStore } from "react";

const SIDEBAR_STORAGE_KEY = "wavequant.sidebar.collapsed.v1";
const SIDEBAR_CHANGE_EVENT = "wavequant:sidebar-change";
const RESEARCH_PAGE_CHANGE_EVENT = "wavequant:research-page-change";
const researchPages = new Set(["health", "orders", "performance", "topology", "workspace"]);
let fallbackSidebarState = false;

function subscribeToSidebar(onChange: () => void): () => void {
    window.addEventListener("storage", onChange);
    window.addEventListener(SIDEBAR_CHANGE_EVENT, onChange);
    return () => {
        window.removeEventListener("storage", onChange);
        window.removeEventListener(SIDEBAR_CHANGE_EVENT, onChange);
    };
}

function getSidebarSnapshot(): boolean {
    try {
        return window.localStorage.getItem(SIDEBAR_STORAGE_KEY) === "true";
    } catch {
        return fallbackSidebarState;
    }
}

function subscribeToResearchPage(onChange: () => void): () => void {
    window.addEventListener("popstate", onChange);
    window.addEventListener(RESEARCH_PAGE_CHANGE_EVENT, onChange);
    return () => {
        window.removeEventListener("popstate", onChange);
        window.removeEventListener(RESEARCH_PAGE_CHANGE_EVENT, onChange);
    };
}

function getResearchPageSnapshot(): string {
    const page = new URLSearchParams(window.location.search).get("page");
    return page && researchPages.has(page) ? page : "workspace";
}

/** 订阅研究工作台 URL 页签，兼容浏览器历史与动态移动导航。 */
export function useResearchPage(): string {
    return useSyncExternalStore(subscribeToResearchPage, getResearchPageSnapshot, () => "workspace");
}

/** 更新可分享的研究页签 URL，并通知同页的桌面与移动导航。 */
export function selectResearchPage(page: string): void {
    const url = new URL(window.location.href);
    url.searchParams.set("page", page);
    window.history.replaceState(null, "", url);
    window.dispatchEvent(new Event(RESEARCH_PAGE_CHANGE_EVENT));
}

/** 订阅带安全降级的侧栏折叠偏好。 */
export function useSidebarCollapsed(): boolean {
    return useSyncExternalStore(subscribeToSidebar, getSidebarSnapshot, () => false);
}

/** 切换并持久化侧栏状态；禁用 Storage 时仍保留当前会话交互。 */
export function toggleSidebarCollapsed(): void {
    const next = !getSidebarSnapshot();
    fallbackSidebarState = next;
    try {
        window.localStorage.setItem(SIDEBAR_STORAGE_KEY, String(next));
    } catch {
        // 浏览器禁用持久化时使用模块内的会话级降级状态。
    }
    window.dispatchEvent(new Event(SIDEBAR_CHANGE_EVENT));
}
