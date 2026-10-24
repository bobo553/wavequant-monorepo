"use client";
import { defineAsyncView } from "@repo/async-ui";
import { AsyncFeatureFallback } from "@/shared/components/async-feature";
const Search = defineAsyncView({ id: "market-search-dialog", source: "src/features/market-dashboard/components/market-search-dialog.tsx",
    loader: () => import(/* webpackChunkName: "market-search-dialog" */ "./market-search-dialog"), fallback: AsyncFeatureFallback });
const Stock = defineAsyncView({ id: "market-stock-dialog", source: "src/features/market-dashboard/components/market-stock-dialog.tsx",
    loader: () => import(/* webpackChunkName: "market-stock-dialog" */ "./market-stock-dialog"), fallback: AsyncFeatureFallback });
const Notifications = defineAsyncView({ id: "market-notifications-dialog", source: "src/features/market-dashboard/components/market-notifications-dialog.tsx",
    loader: () => import(/* webpackChunkName: "market-notifications-dialog" */ "./market-notifications-dialog"), fallback: AsyncFeatureFallback });
const Info = defineAsyncView({ id: "market-info-dialog", source: "src/features/market-dashboard/components/market-info-dialog.tsx",
    loader: () => import(/* webpackChunkName: "market-info-dialog" */ "./market-info-dialog"), fallback: AsyncFeatureFallback });
const Settings = defineAsyncView({ id: "market-settings-dialog", source: "src/features/market-dashboard/components/market-settings-panel.tsx",
    loader: () => import(/* webpackChunkName: "market-settings-dialog" */ "./market-settings-panel").then((module) => ({ default: module.MarketSettingsPanel })), fallback: AsyncFeatureFallback });
export const marketDialogViews = { search: Search, stock: Stock, notifications: Notifications, info: Info, settings: Settings };
