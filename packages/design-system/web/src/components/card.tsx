import * as React from "react";

import { cn } from "@/shared/utils/cn";

/** 承载一组相关信息的基础卡片容器。 */
function Card({ className, ...props }: React.ComponentProps<"section">): React.ReactElement {
    return (
        <section
            data-slot="card"
            className={cn("border-surface-400/70 bg-card text-card-foreground rounded-xl border shadow-sm", className)}
            {...props}
        />
    );
}

/** 卡片标题区域。 */
function CardHeader({ className, ...props }: React.ComponentProps<"header">): React.ReactElement {
    return <header data-slot="card-header" className={cn("flex flex-col gap-1.5 p-5", className)} {...props} />;
}

/** 卡片主标题。 */
function CardTitle({ className, ...props }: React.ComponentProps<"h2">): React.ReactElement {
    return <h2 data-slot="card-title" className={cn("font-semibold tracking-tight", className)} {...props} />;
}

/** 卡片辅助说明。 */
function CardDescription({ className, ...props }: React.ComponentProps<"p">): React.ReactElement {
    return <p data-slot="card-description" className={cn("text-muted-foreground text-sm", className)} {...props} />;
}

/** 卡片主体内容。 */
function CardContent({ className, ...props }: React.ComponentProps<"div">): React.ReactElement {
    return <div data-slot="card-content" className={cn("px-5 pb-5", className)} {...props} />;
}

/** 卡片底部操作区域。 */
function CardFooter({ className, ...props }: React.ComponentProps<"footer">): React.ReactElement {
    return <footer data-slot="card-footer" className={cn("flex items-center px-5 pb-5", className)} {...props} />;
}

export { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle };
