import { stdin, stdout } from "node:process";
import { createInterface } from "node:readline/promises";
import { parseArgs } from "node:util";

import { createProject, findRepositoryRoot } from "./generator.js";
import { isTemplateId, templateDefinitions } from "./templates.js";
import type { TTemplateId } from "./types.js";
import { templateIds } from "./types.js";

const helpText = `Create a workspace from a repository template.

Usage:
  pnpm create:project
  pnpm create:project -- --template <template> --name <name> [--port <port>] [--dry-run]

Options:
  -t, --template <id>  h5 | admin | api | mobile
  -n, --name <name>    kebab-case workspace name
  -p, --port <port>    H5/Admin port; automatically selected when omitted
      --dry-run        Validate and print the plan without writing files
  -l, --list           List available templates
  -h, --help           Show this help
`;

const listTemplates = () => templateIds.map((id) => `  ${id.padEnd(8)} ${templateDefinitions[id].label}`).join("\n");

const parsePort = (value: string | undefined) => {
    if (value === undefined) return undefined;
    if (!/^\d+$/.test(value)) throw new Error("--port 必须是整数");
    return Number(value);
};

const askForTemplate = async (question: (prompt: string) => Promise<string>) => {
    stdout.write(`Available templates:\n${listTemplates()}\n`);
    const answer = (await question("Template: ")).trim();
    if (!isTemplateId(answer)) throw new Error(`未知模板：${answer}`);
    return answer;
};

const run = async () => {
    const { values } = parseArgs({
        allowPositionals: false,
        options: {
            "dry-run": { type: "boolean", default: false },
            help: { type: "boolean", short: "h", default: false },
            list: { type: "boolean", short: "l", default: false },
            name: { type: "string", short: "n" },
            port: { type: "string", short: "p" },
            template: { type: "string", short: "t" },
        },
        strict: true,
    });

    if (values.help) {
        stdout.write(helpText);
        return;
    }
    if (values.list) {
        stdout.write(`${listTemplates()}\n`);
        return;
    }

    let readline: ReturnType<typeof createInterface> | undefined;
    try {
        let template: TTemplateId | undefined;
        if (values.template !== undefined) {
            if (!isTemplateId(values.template)) throw new Error(`未知模板：${values.template}`);
            template = values.template;
        }

        let name = values.name?.trim();
        if (!template || !name) {
            if (!stdin.isTTY) {
                throw new Error("非交互环境必须提供 --template 和 --name");
            }
            readline = createInterface({ input: stdin, output: stdout });
            const question = (prompt: string) => readline!.question(prompt);
            template ??= await askForTemplate(question);
            name ||= (await question("Project name (kebab-case): ")).trim();
        }

        const repositoryRoot = await findRepositoryRoot();
        const result = await createProject({
            repositoryRoot,
            template,
            name,
            port: parsePort(values.port),
            dryRun: values["dry-run"],
        });

        const portSummary = result.port === undefined ? "" : `\nPort: ${result.port}`;
        if (!result.created) {
            stdout.write(
                `Dry run passed.\nTemplate: ${result.template.id}\nTarget: ${result.targetRelativePath}${portSummary}\n`,
            );
            return;
        }

        stdout.write(
            `Created ${result.targetRelativePath} from ${result.template.id} (${result.copiedFileCount} files).${portSummary}\n\nNext steps:\n  pnpm install\n  pnpm --filter ${result.name} ${result.template.startScript}\n`,
        );
    } finally {
        readline?.close();
    }
};

try {
    await run();
} catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    process.stderr.write(`Project creation failed: ${message}\n`);
    process.exitCode = 1;
}
