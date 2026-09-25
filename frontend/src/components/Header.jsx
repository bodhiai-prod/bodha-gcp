import React from "react";
import { Link, NavLink, useNavigate } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import { SignOut, UserCircle } from "@phosphor-icons/react";

const NavItem = ({ to, label, testid }) => (
  <NavLink
    to={to}
    end
    data-testid={testid}
    className={({ isActive }) =>
      `whitespace-nowrap px-2 py-2 text-xs font-bold uppercase tracking-wide transition-colors xl:px-3 xl:text-sm xl:tracking-wider ${
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

  return (
    <header className="border-b-2 border-black bg-white sticky top-0 z-40">
      <div className="mx-auto flex w-full items-center gap-4 px-5 py-3 sm:px-8 xl:px-12 2xl:px-16">
        <Link
          to={user?.role === "student" ? "/student" : "/"}
          data-testid="app-logo-link"
          className="flex shrink-0 items-center gap-3 group"
        >
          <img
            src="/assets/bodhaai-app-icon-512.png"
            alt="Bodha AI"
            className="w-10 h-10 rounded-[0.55rem] transition-transform group-hover:scale-105"
          />
          <div className="leading-none">
            <div className="brand-mark text-xl">
              Bodha <span className="accent">AI</span>
            </div>
            <div className="overline text-neutral-500 text-[10px] mt-0.5">
              Question Paper Studio
            </div>
          </div>
        </Link>

        <nav className="hidden min-w-0 flex-1 items-center justify-center gap-0 md:flex xl:gap-2">
          {user?.role === "student" ? (
            <>
              <NavItem to="/student" label="My Tests" testid="nav-student-dashboard" />
              <NavItem
                to="/student/mock-tests/new"
                label="New Mock Test"
                testid="nav-new-mock-test"
              />
            </>
          ) : (
            <>
              <NavItem to="/" label="Dashboard" testid="nav-dashboard" />
              <NavItem to="/textbooks" label="Textbooks" testid="nav-textbooks" />
              <NavItem to="/papers/new" label="New Paper" testid="nav-new-paper" />
              <NavItem to="/papers" label="Papers" testid="nav-papers" />
              <NavItem to="/competitive-exams" label="Competitive" testid="nav-competitive" />
              <NavItem to="/qbank" label="Question Bank" testid="nav-qbank" />
              <NavItem to="/settings/branding" label="Branding" testid="nav-branding" />
              {user?.role === "admin" && (
                <NavItem to="/admin" label="Admin" testid="nav-admin" />
              )}
            </>
          )}
        </nav>

        <div className="flex shrink-0 items-center gap-2 xl:gap-3">
          <div className="hidden xl:block whitespace-nowrap text-right">
            <div className="text-sm font-bold">{user?.full_name}</div>
            <div className="overline text-neutral-500 text-[10px]">
              {user?.role}
            </div>
          </div>
          <Link to="/account" data-testid="nav-account" className="qp-btn qp-btn-secondary" aria-label="Account"><UserCircle size={18} weight="bold" /><span className="hidden sm:inline">Account</span></Link>
          <button
            onClick={handleLogout}
            data-testid="logout-button"
            className="qp-btn qp-btn-secondary"
          >
            <SignOut size={16} weight="bold" />
            <span className="hidden sm:inline">Logout</span>
          </button>
        </div>
      </div>
    </header>
  );
}
