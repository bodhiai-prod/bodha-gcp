import React from "react";
import { Link, NavLink, useNavigate } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import { List, SignOut, UserCircle } from "@phosphor-icons/react";
import {
  Sheet,
  SheetClose,
  SheetContent,
  SheetDescription,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";

const NavItem = ({ to, label, testid, onClick, drawer = false }) => (
  <NavLink
    to={to}
    end
    data-testid={testid}
    onClick={onClick}
    className={({ isActive }) =>
      `${drawer ? "w-full border-2 border-black px-4 py-3 text-left text-sm" : "whitespace-nowrap px-3 py-2 text-sm tracking-wider"} font-bold uppercase tracking-wide transition-colors ${
        isActive
          ? "bg-black text-white"
          : "text-black hover:bg-neutral-100"
      }`
    }
  >
    {label}
  </NavLink>
);

export default function Header() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  const handleLogout = () => {
    logout();
    navigate("/login");
  };

  const navItems = user?.role === "student"
    ? [
        { to: "/student", label: "My Tests", testid: "nav-student-dashboard" },
        { to: "/student/mock-tests/new", label: "New Mock Test", testid: "nav-new-mock-test" },
      ]
    : [
        { to: "/", label: "Dashboard", testid: "nav-dashboard" },
        { to: "/textbooks", label: "Textbooks", testid: "nav-textbooks" },
        { to: "/papers/new", label: "New Paper", testid: "nav-new-paper" },
        { to: "/papers", label: "Papers", testid: "nav-papers" },
        { to: "/competitive-exams", label: "Competitive", testid: "nav-competitive" },
        { to: "/qbank", label: "Question Bank", testid: "nav-qbank" },
        { to: "/settings/branding", label: "Branding", testid: "nav-branding" },
        ...(user?.role === "admin" ? [{ to: "/admin", label: "Admin", testid: "nav-admin" }] : []),
      ];

  return (
    <header className="border-b-2 border-black bg-white sticky top-0 z-40">
      <div className="mx-auto flex w-full min-w-0 items-center gap-2 px-3 py-3 sm:gap-4 sm:px-8 2xl:px-16">
        <Link
          to={user?.role === "student" ? "/student" : "/"}
          data-testid="app-logo-link"
          className="flex min-w-0 shrink items-center gap-2 sm:gap-3 group"
        >
          <img
            src="/assets/bodhaai-app-icon-512.png"
            alt="Bodha AI"
            className="w-10 h-10 rounded-[0.55rem] transition-transform group-hover:scale-105"
          />
          <div className="min-w-0 leading-none">
            <div className="brand-mark whitespace-nowrap text-lg sm:text-xl">
              Bodha <span className="accent">AI</span>
            </div>
            <div className="overline mt-0.5 hidden text-[10px] text-neutral-500 sm:block">
              Question Paper Studio
            </div>
          </div>
        </Link>

        <nav className="hidden min-w-0 flex-1 items-center justify-center gap-1 2xl:flex" aria-label="Primary navigation">
          {navItems.map((item) => <NavItem key={item.to} {...item} />)}
        </nav>

        <div className="ml-auto flex shrink-0 items-center gap-1 sm:gap-2 2xl:ml-0 2xl:gap-3">
          <div className="hidden 2xl:block whitespace-nowrap text-right">
            <div className="text-sm font-bold">{user?.full_name}</div>
            <div className="overline text-neutral-500 text-[10px]">
              {user?.role}
            </div>
          </div>
          <Link to="/account" data-testid="nav-account" className="qp-btn qp-btn-secondary px-3 sm:px-4" aria-label="Account"><UserCircle size={18} weight="bold" /><span className="hidden sm:inline">Account</span></Link>
          <button
            onClick={handleLogout}
            data-testid="logout-button"
            className="qp-btn qp-btn-secondary px-3 sm:px-4"
          >
            <SignOut size={16} weight="bold" />
            <span className="hidden sm:inline">Logout</span>
          </button>
          <Sheet>
            <SheetTrigger asChild>
              <button type="button" className="qp-btn qp-btn-secondary px-3 2xl:hidden" aria-label="Open navigation menu" data-testid="nav-menu-button">
                <List size={20} weight="bold" />
                <span className="sr-only">Menu</span>
              </button>
            </SheetTrigger>
            <SheetContent side="right" className="w-[min(22rem,calc(100vw-1rem))] border-l-2 border-black bg-[#FAFAFA] p-5">
              <SheetTitle className="font-display text-2xl">Menu</SheetTitle>
              <SheetDescription className="sr-only">Application navigation and account actions</SheetDescription>
              <nav className="mt-7 space-y-2" aria-label="Mobile navigation">
                {navItems.map((item) => (
                  <SheetClose key={item.to} asChild>
                    <NavItem {...item} drawer />
                  </SheetClose>
                ))}
              </nav>
              <div className="mt-6 grid gap-2 border-t-2 border-black pt-5">
                <SheetClose asChild>
                  <Link to="/account" className="qp-btn qp-btn-secondary w-full" data-testid="drawer-account">
                    <UserCircle size={18} weight="bold" /> Account
                  </Link>
                </SheetClose>
                <button onClick={handleLogout} className="qp-btn qp-btn-secondary w-full" data-testid="drawer-logout">
                  <SignOut size={16} weight="bold" /> Logout
                </button>
              </div>
            </SheetContent>
          </Sheet>
        </div>
      </div>
    </header>
  );
}
