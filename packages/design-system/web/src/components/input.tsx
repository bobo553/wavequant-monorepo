import * as React from "react";

import { cn } from "@/shared/utils/cn";

/** 适用于表单和搜索场景的基础输入框。 */
function Input({ className, type = "text", ...props }: React.ComponentProps<"input">): React.ReactElement {
    return (
        <input
            type={type}
            data-slot="input"
            className={cn(
                "border-surface-400 bg-background placeholder:text-muted-foreground focus-visible:border-primary focus-visible:ring-ring/25 h-9 w-full rounded-md border px-3 py-1 text-sm shadow-xs transition-colors outline-none focus-visible:ring-2 disabled:cursor-not-allowed disabled:opacity-50",
                className,
            )}
            {...props}
        />
    );
}

export { Input };
