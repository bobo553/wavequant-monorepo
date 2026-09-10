import eslintReact from "@eslint-react/eslint-plugin";
import type { Linter } from "eslint";
import pluginReactHooks from "eslint-plugin-react-hooks";
import globals from "globals";

import { baseConfig } from "./base";

const toLinterConfig = (config: unknown): Linter.Config => config as Linter.Config;
type ConfigPlugin = NonNullable<Linter.Config["plugins"]>[string];

const reactRecommendedConfig = toLinterConfig(eslintReact.configs["recommended-typescript"]);
const reactHooksPlugin = pluginReactHooks as unknown as ConfigPlugin;

export const reactConfig: Linter.Config[] = [
    ...baseConfig,
    {
        ...reactRecommendedConfig,
        languageOptions: {
            ...reactRecommendedConfig.languageOptions,
            globals: {
                ...globals.serviceworker,
            },
        },
    },
    {
        plugins: {
            "react-hooks": reactHooksPlugin,
        },
        rules: {
            ...pluginReactHooks.configs.recommended.rules,
        },
    },
    {
        ignores: ["next-env.d.ts", "node_modules/**", "dist/**", "build/**", "coverage/**"],
    },
];
