"use client";

import { type JSX, useSyncExternalStore } from "react";

import { Button } from "@repo/design-system-web/components";
import { IconMoon, IconSun } from "@tabler/icons-react";
import { useTheme } from "next-themes";

/** 切换管理后台的明暗主题，并避免服务端水合闪烁。 */
export function ThemeToggle(): JSX.Element {
    const mounted = useSyncExternalStore(
        () => () => undefined,
        () => true,
        () => false,
    );
    const { resolvedTheme, setTheme } = useTheme();

    const isDark = mounted && resolvedTheme === "dark";

    return (
        <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label={isDark ? "切换为浅色模式" : "切换为深色模式"}
            onClick={() => setTheme(isDark ? "light" : "dark")}
        >
            {isDark ? <IconSun aria-hidden="true" /> : <IconMoon aria-hidden="true" />}
        </Button>
    );
}
