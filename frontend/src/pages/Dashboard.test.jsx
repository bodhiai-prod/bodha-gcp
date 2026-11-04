import React, { act } from "react";
import { createRoot } from "react-dom/client";
import Dashboard from "./Dashboard";
import { api } from "@/lib/api";

jest.mock("@/lib/api", () => ({ api: { get: jest.fn(), post: jest.fn() } }));
jest.mock("@/components/Header", () => () => null);
jest.mock("@/components/PaperStatusBadge", () => () => null);
jest.mock("@/context/AuthContext", () => ({ useAuth: () => ({ user: { full_name: "Taylor" } }) }));
jest.mock("@/context/ConfirmationContext", () => ({ useConfirmation: () => jest.fn() }));
jest.mock("sonner", () => ({ toast: { success: jest.fn(), error: jest.fn() } }));
jest.mock("react-router-dom", () => ({
  Link: ({ to, children, ...props }) => <a href={to} {...props}>{children}</a>,
  useNavigate: () => jest.fn(),
}));

let container;
let root;

beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  api.get.mockImplementation((url) => Promise.resolve({
    data: url === "/papers" ? [] : [],
  }));
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  jest.clearAllMocks();
});

test("recent papers view-all link opens the generated papers route", async () => {
  await act(async () => root.render(<Dashboard />));

  const link = container.querySelector('[data-testid="link-view-all-papers"]');
  expect(link.textContent).toBe("View all");
  expect(link.getAttribute("href")).toBe("/papers");
  expect([...container.querySelectorAll("h2")].map((heading) => heading.textContent)).toEqual([
    "Recent papers",
    "Quick access",
  ]);
  expect(container.textContent).not.toContain("//");
});
