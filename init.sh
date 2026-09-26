#!/usr/bin/env bash
set -euo pipefail

mode="quick"
if [[ "${1:-}" == "--mode" ]]; then
  mode="${2:-quick}"
fi

cd "$(dirname "$0")"

node --version
pnpm --version

if [[ ! -d node_modules ]]; then
  pnpm install --frozen-lockfile
fi

if [[ "$mode" == "full" ]]; then
  pnpm verify:quick
  pnpm build
else
  pnpm verify:quick
fi
