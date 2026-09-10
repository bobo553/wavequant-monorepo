import type { ITemplateDefinition, TTemplateId } from "./types.js";
import { templateIds } from "./types.js";

export const templateDefinitions: Readonly<Record<TTemplateId, ITemplateDefinition>> = {
    h5: {
        id: "h5",
        label: "Consumer H5",
        sourceDirectory: "apps/webs/h5",
        targetDirectory: "apps/webs",
        kind: "web",
        startScript: "dev",
    },
    admin: {
        id: "admin",
        label: "Admin dashboard",
        sourceDirectory: "apps/webs/admin",
        targetDirectory: "apps/webs",
        kind: "web",
        startScript: "dev",
    },
    api: {
        id: "api",
        label: "NestJS API",
        sourceDirectory: "apps/servers/api",
        targetDirectory: "apps/servers",
        kind: "server",
        startScript: "dev",
    },
    mobile: {
        id: "mobile",
        label: "Expo mobile",
        sourceDirectory: "apps/mobiles/mobile",
        targetDirectory: "apps/mobiles",
        kind: "mobile",
        startScript: "start",
    },
};

export const isTemplateId = (value: string): value is TTemplateId => templateIds.includes(value as TTemplateId);
