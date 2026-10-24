"use client";
import { defineAsyncView } from "@repo/async-ui";
import { AsyncFeatureFallback } from "@/shared/components/async-feature";
export const AsyncOverview = defineAsyncView({
    id: "market-overview-view", source: "src/features/market-dashboard/components/overview-view.tsx",
    loader: () => import(/* webpackChunkName: "market-overview-view" */ "./components/overview-view").then((module) => ({ default: module.OverviewView })),
    fallback: AsyncFeatureFallback,
});
export const AsyncCatalog = defineAsyncView({
    id: "market-all-stocks-catalog", source: "src/features/market-dashboard/components/all-stocks-catalog.tsx",
    loader: () => import(/* webpackChunkName: "market-all-stocks-catalog" */ "./components/all-stocks-catalog").then((module) => ({ default: module.AllStocksCatalog })),
    fallback: AsyncFeatureFallback,
});
export const AsyncLadder = defineAsyncView({
    id: "market-ladder-view", source: "src/features/market-dashboard/components/ladder-view.tsx",
    loader: () => import(/* webpackChunkName: "market-ladder-view" */ "./components/ladder-view").then((module) => ({ default: module.LadderView })),
    fallback: AsyncFeatureFallback,
});
export const AsyncSector = defineAsyncView({
    id: "market-sector-view", source: "src/features/market-dashboard/components/sector-view.tsx",
    loader: () => import(/* webpackChunkName: "market-sector-view" */ "./components/sector-view").then((module) => ({ default: module.SectorView })),
    fallback: AsyncFeatureFallback,
});
export const AsyncTheme = defineAsyncView({
    id: "market-theme-view", source: "src/features/market-dashboard/components/theme-view.tsx",
    loader: () => import(/* webpackChunkName: "market-theme-view" */ "./components/theme-view").then((module) => ({ default: module.ThemeView })),
    fallback: AsyncFeatureFallback,
});
export const AsyncLeader = defineAsyncView({
    id: "market-leader-view", source: "src/features/market-dashboard/components/leader-view.tsx",
    loader: () => import(/* webpackChunkName: "market-leader-view" */ "./components/leader-view").then((module) => ({ default: module.LeaderView })),
    fallback: AsyncFeatureFallback,
});
export const AsyncRadar = defineAsyncView({
    id: "market-radar-view", source: "src/features/market-dashboard/components/radar-view.tsx",
    loader: () => import(/* webpackChunkName: "market-radar-view" */ "./components/radar-view").then((module) => ({ default: module.RadarView })),
    fallback: AsyncFeatureFallback,
});
export const AsyncMulti = defineAsyncView({
    id: "market-multi-stock-view", source: "src/features/market-dashboard/components/multi-stock-view.tsx",
    loader: () => import(/* webpackChunkName: "market-multi-stock-view" */ "./components/multi-stock-view").then((module) => ({ default: module.MultiStockView })),
    fallback: AsyncFeatureFallback,
});
export const AsyncReview = defineAsyncView({
    id: "market-review-view", source: "src/features/market-dashboard/components/review-view.tsx",
    loader: () => import(/* webpackChunkName: "market-review-view" */ "./components/review-view").then((module) => ({ default: module.ReviewView })),
    fallback: AsyncFeatureFallback,
});
