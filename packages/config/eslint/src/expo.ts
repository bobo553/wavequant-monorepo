import eslintReact from "@eslint-react/eslint-plugin";
import type { Linter } from "eslint";
import pluginReactHooks from "eslint-plugin-react-hooks";

import { baseConfig } from "./base";

const toLinterConfig = (config: unknown): Linter.Config => config as Linter.Config;
type ConfigPlugin = NonNullable<Linter.Config["plugins"]>[string];

const reactRecommendedConfig = toLinterConfig(eslintReact.configs["recommended-typescript"]);
const reactHooksPlugin = pluginReactHooks as unknown as ConfigPlugin;

export const expoConfig: Linter.Config[] = [
    ...baseConfig,
    {
        ...reactRecommendedConfig,
    },
    {
        plugins: {
            "react-hooks": reactHooksPlugin,
        },
        rules: {
            ...pluginReactHooks.configs.recommended.rules,
            "react-hooks/exhaustive-deps": "warn",
        },
    },
    {
        ignores: ["node_modules/**", "dist/**", "build/**", "coverage/**", "android/**", "ios/**", ".expo/**"],
    },
];
