# Create Project CLI

Creates a new workspace from one of the repository's maintained application templates.

## Usage

Interactive:

```bash
pnpm create:project
```

Non-interactive:

```bash
pnpm create:project -- --template h5 --name campaign-share
pnpm create:project -- --template admin --name operations-admin --port 3010
pnpm create:project -- --template api --name billing-api
pnpm create:project -- --template mobile --name field-ops
```

Inspect a plan without writing files:

```bash
pnpm create:project -- --template h5 --name campaign-share --dry-run
```

List templates:

```bash
pnpm create:project -- --list
```

## Safety and generated changes

- Project names must use kebab-case and cannot collide with an existing workspace.
- Targets are restricted to the selected template's `apps/*` category.
- Existing targets are never overwritten.
- `node_modules`, build output, caches, coverage, test reports and real `.env.*` files are excluded.
- H5/Admin ports are checked against existing Next.js workspaces and allocated automatically when omitted.
- `package.json`, README, progress, Docker paths, Playwright URL and Expo identifiers are rewritten when applicable.
- The CLI never installs dependencies or executes generated project scripts.

After generation, review the diff and run:

```bash
pnpm install
pnpm --filter <workspace-name> lint
pnpm --filter <workspace-name> typecheck
pnpm --filter <workspace-name> test:unit
pnpm --filter <workspace-name> build
```
