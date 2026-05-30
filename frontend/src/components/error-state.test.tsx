import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { ErrorState } from "@/components/error-state";

// Component smoke tests — proves the Vitest + jsdom + Testing Library wiring
// renders a React 19 component and queries it (H10).
describe("ErrorState", () => {
  it("renders the message with the default title", () => {
    render(<ErrorState message="The source PDF didn't load" />);
    expect(screen.getByText("The source PDF didn't load")).toBeTruthy();
    expect(screen.getByText("Something's not right")).toBeTruthy();
  });

  it("renders a custom title and action", () => {
    render(<ErrorState title="Workspace expired" message="Upload again" action={<button>Retry</button>} />);
    expect(screen.getByText("Workspace expired")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();
  });
});
