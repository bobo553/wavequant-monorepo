import type { JSX } from "react";

import { ResourcePlaceholder } from "@/shared/components/resource-placeholder/resource-placeholder";

/** 订单管理模板页。 */
export default function OrdersPage(): JSX.Element {
    return (
        <ResourcePlaceholder
            title="订单管理"
            description="集中处理订单审核、履约、售后与导出任务。"
            emptyTitle="还没有可展示的订单"
            emptyDescription="接入订单查询接口后，可在这里组合筛选器、批量操作和服务端分页表格。"
        />
    );
}
