import { nextConfig } from "@repo/eslint/next";

/** @type {import("eslint").Linter.Config[]} */
export default [
    ...nextConfig,
    {
        ignores: ["public/**", "tests/*.cjs", "tests/*.mjs"],
    },
];
