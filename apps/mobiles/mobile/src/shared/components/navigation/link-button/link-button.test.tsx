import type { ReactElement, ReactNode } from "react";

import { Linking } from "react-native";

import type { TIcon } from "@repo/design-system-mobile/components";

import { LinkButton } from "./link-button";

vi.mock("react-native", () => ({
    Linking: { openURL: vi.fn().mockResolvedValue(null) },
}));

vi.mock("@repo/design-system-mobile/components", () => ({
    Button: () => null,
}));

interface IRenderedButtonProps {
    children: ReactNode;
    className?: string;
    onPress: () => Promise<void>;
}

const mockIcon = { icon: () => null } as unknown as TIcon;

const mockProps = {
    href: "https://github.com/bobo553/monorepo-template",
    label: "Explore Docs",
    icon: mockIcon,
};

function renderLinkButton(className?: string): ReactElement<IRenderedButtonProps> {
    return LinkButton({ ...mockProps, className }) as ReactElement<IRenderedButtonProps>;
}

describe("LinkButton", () => {
    beforeEach(() => {
        vi.clearAllMocks();
    });

    it("renders the label text", () => {
        expect(renderLinkButton().props.children).toBe(mockProps.label);
    });

    it("calls Linking.openURL with the correct href when pressed", async () => {
        await renderLinkButton().props.onPress();

        expect(Linking.openURL).toHaveBeenCalledWith(mockProps.href);
        expect(Linking.openURL).toHaveBeenCalledTimes(1);
    });

    it("does not call Linking.openURL before being pressed", () => {
        renderLinkButton();

        expect(Linking.openURL).not.toHaveBeenCalled();
    });

    it("passes the className prop to the underlying Button", () => {
        expect(renderLinkButton("custom-class").props.className).toBe("custom-class");
    });
});
