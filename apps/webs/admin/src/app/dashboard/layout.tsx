import type { JSX, ReactNode } from "react";

import { AdminShell } from "@/shared/components/admin-shell/admin-shell";

/** 为全部后台业务路由提供统一应用壳。 */
export default function DashboardLayout({ children }: Readonly<{ children: ReactNode }>): JSX.Element {
    return <AdminShell>{children}</AdminShell>;
}
