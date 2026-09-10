"use client";

import type { JSX, ReactNode } from "react";

import { ThemeProvider as NextThemeProvider } from "next-themes";

/** 为后台提供系统主题感知和明暗模式切换。 */
export function ThemeProvider({ children }: Readonly<{ children: ReactNode }>): JSX.Element {
    return (
        <NextThemeProvider attribute="class" defaultTheme="system" enableSystem disableTransitionOnChange>
            {children}
        </NextThemeProvider>
    );
}
