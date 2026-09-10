import * as React from "react";

import { cn } from "@/shared/utils/cn";
import { type VariantProps, cva } from "class-variance-authority";

const badgeVariants = cva(
    "inline-flex w-fit shrink-0 items-center justify-center rounded-full border px-2.5 py-0.5 text-xs font-semibold whitespace-nowrap transition-colors",
    {
        variants: {
            variant: {
                default: "border-transparent bg-primary text-white",
                secondary: "border-transparent bg-secondary text-secondary-foreground",
                outline: "border-surface-400 bg-transparent text-foreground",
                success: "border-emerald-500/20 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
                warning: "border-amber-500/20 bg-amber-500/10 text-amber-700 dark:text-amber-300",
            },
        },
        defaultVariants: { variant: "default" },
    },
);

/** 用于展示状态、分类或简短提示的徽标。 */
function Badge({
    className,
    variant,
    ...props
}: React.ComponentProps<"span"> & VariantProps<typeof badgeVariants>): React.ReactElement {
    return <span data-slot="badge" className={cn(badgeVariants({ variant }), className)} {...props} />;
}

export { Badge, badgeVariants };
