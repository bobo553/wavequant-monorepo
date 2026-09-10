import { baseConfig } from "@repo/eslint/base";

export default [
    ...baseConfig,
    {
        files: ["**/*.ts"],
        rules: {
            "no-undef": "off",
        },
    },
];
