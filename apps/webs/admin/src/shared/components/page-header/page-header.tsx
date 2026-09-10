import type { JSX, ReactNode } from "react";

interface IPageHeaderProps {
    actions?: ReactNode;
    description: string;
    eyebrow?: string;
    title: string;
}

/** 统一业务页面的标题、说明和操作区布局。 */
export function PageHeader({ actions, description, eyebrow, title }: IPageHeaderProps): JSX.Element {
    return (
        <header className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
            <div className="space-y-1">
                {eyebrow ? (
                    <p className="text-primary text-xs font-semibold tracking-[0.18em] uppercase">{eyebrow}</p>
                ) : null}
                <h1 className="text-2xl font-bold tracking-tight sm:text-3xl">{title}</h1>
                <p className="text-muted-foreground max-w-2xl text-sm leading-6">{description}</p>
            </div>
            {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
        </header>
    );
}
