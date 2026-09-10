import type { JSX } from "react";

import { ResourcePlaceholder } from "@/shared/components/resource-placeholder/resource-placeholder";

/** 客户管理模板页。 */
export default function CustomersPage(): JSX.Element {
    return (
        <ResourcePlaceholder
            title="客户管理"
            description="管理企业客户档案、分层标签和跟进状态。"
            emptyTitle="客户列表等待接入"
            emptyDescription="建议通过 @repo/contracts 定义查询参数和响应结构，再接入真实客户服务。"
        />
    );
}
