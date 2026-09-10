import * as React from "react";

import { cn } from "@/shared/utils/cn";

/** 在内容组之间提供可访问的视觉分隔。 */
function Separator({ className, ...props }: React.ComponentProps<"div">): React.ReactElement {
    return (
        <div
            role="separator"
            data-slot="separator"
            className={cn("bg-surface-400/70 h-px w-full", className)}
            {...props}
        />
    );
}

export { Separator };
