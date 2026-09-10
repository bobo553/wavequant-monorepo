import * as React from "react";

import { cn } from "@/shared/utils/cn";

/** 在内容加载期间维持布局稳定的骨架占位。 */
function Skeleton({ className, ...props }: React.ComponentProps<"div">): React.ReactElement {
    return (
        <div
            aria-hidden="true"
            data-slot="skeleton"
            className={cn("bg-surface-500/70 animate-pulse rounded-md", className)}
            {...props}
        />
    );
}

export { Skeleton };
