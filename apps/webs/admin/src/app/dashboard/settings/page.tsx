import type { JSX } from "react";

import { ResourcePlaceholder } from "@/shared/components/resource-placeholder/resource-placeholder";

/** 系统设置模板页。 */
export default function SettingsPage(): JSX.Element {
    return (
        <ResourcePlaceholder
            title="系统设置"
            description="管理组织偏好、成员权限和外部服务集成。"
            emptyTitle="选择一个配置模块"
            emptyDescription="接入权限系统后，可按角色展示组织、安全、通知和集成设置。"
        />
    );
}
