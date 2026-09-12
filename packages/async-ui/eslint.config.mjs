import { reactConfig } from "@repo/eslint/react";

export default [
    ...reactConfig,
    {
        rules: {
            // 公共 API 沿用 React 生态与需求文档中的无前缀类型命名。
            "@typescript-eslint/naming-convention": "off",
        },
    },
];
