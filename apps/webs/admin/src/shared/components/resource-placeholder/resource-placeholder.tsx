import type { JSX } from "react";

import { PageHeader } from "@/shared/components/page-header/page-header";
import { Badge, Button, Card, CardContent } from "@repo/design-system-web/components";
import { IconPlus, IconSearch } from "@tabler/icons-react";

interface IResourcePlaceholderProps {
    description: string;
    emptyDescription: string;
    emptyTitle: string;
    title: string;
}

/** 为待接入业务数据的标准资源页提供完整空状态。 */
export function ResourcePlaceholder({
    description,
    emptyDescription,
    emptyTitle,
    title,
}: IResourcePlaceholderProps): JSX.Element {
    return (
        <div className="space-y-6">
            <PageHeader
                eyebrow="Business"
                title={title}
                description={description}
                actions={
                    <Button>
                        <IconPlus aria-hidden="true" />
                        新建
                    </Button>
                }
            />
            <Card className="min-h-96">
                <CardContent className="flex min-h-96 flex-col items-center justify-center px-6 text-center">
                    <span className="bg-primary/10 text-primary mb-4 grid size-12 place-items-center rounded-xl">
                        <IconSearch aria-hidden="true" />
                    </span>
                    <Badge variant="outline">等待接入数据源</Badge>
                    <h2 className="mt-4 text-lg font-semibold">{emptyTitle}</h2>
                    <p className="text-muted-foreground mt-2 max-w-md text-sm leading-6">{emptyDescription}</p>
                </CardContent>
            </Card>
        </div>
    );
}
