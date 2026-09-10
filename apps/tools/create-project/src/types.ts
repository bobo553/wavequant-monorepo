export const templateIds = ["h5", "admin", "api", "mobile"] as const;

export type TTemplateId = (typeof templateIds)[number];

export type TTemplateKind = "web" | "server" | "mobile";

export interface ITemplateDefinition {
    readonly id: TTemplateId;
    readonly label: string;
    readonly sourceDirectory: string;
    readonly targetDirectory: string;
    readonly kind: TTemplateKind;
    readonly startScript: string;
}

export interface ICreateProjectOptions {
    readonly repositoryRoot: string;
    readonly template: TTemplateId;
    readonly name: string;
    readonly port?: number;
    readonly dryRun?: boolean;
}

export interface IProjectPlan {
    readonly repositoryRoot: string;
    readonly template: ITemplateDefinition;
    readonly name: string;
    readonly sourcePath: string;
    readonly targetPath: string;
    readonly targetRelativePath: string;
    readonly port?: number;
}

export interface ICreateProjectResult extends IProjectPlan {
    readonly created: boolean;
    readonly copiedFileCount: number;
}
