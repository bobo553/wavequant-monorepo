import type { JSX } from "react";

import { ResourcePlaceholder } from "@/shared/components/resource-placeholder/resource-placeholder";

/** 商品管理模板页。 */
export default function ProductsPage(): JSX.Element {
    return (
        <ResourcePlaceholder
            title="商品管理"
            description="维护商品资料、上下架状态和企业定价策略。"
            emptyTitle="尚未创建商品"
            emptyDescription="可在此基础上扩展商品表格、分类筛选、批量上架与编辑抽屉。"
        />
    );
}
