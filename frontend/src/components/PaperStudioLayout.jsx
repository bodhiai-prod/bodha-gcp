import React from "react";
import { Link, NavLink, useNavigate } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import { Stack, SquaresFour, FileText, Books, BookmarkSimple, Trophy, SignOut, ShieldCheck, CaretRight } from "@phosphor-icons/react";
import "@/styles/paper-studio.css";

export default function PaperStudioLayout({ children }) {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const links = user?.role === "student" ? [
    { to: "/student", label: "My tests", icon: SquaresFour },
    { to: "/student/mock-tests/new", label: "New mock test", icon: FileText },
    { to: "/textbooks", label: "Textbooks", icon: Books },
  ] : [
    { to: "/", label: "Dashboard", icon: SquaresFour },
    { to: "/papers/new", label: "Question papers", icon: FileText },
    { to: "/textbooks", label: "Textbooks", icon: Books },
    { to: "/qbank", label: "Question bank", icon: BookmarkSimple },
    { to: "/competitive-exams", label: "Competitive exams", icon: Trophy },
    ...(user?.role === "admin" ? [{ to: "/admin", label: "Administration", icon: ShieldCheck }] : []),
  ];
  return (
    <div className="paper-studio">
      <aside className="studio-sidebar no-print">
        <Link to={user?.role === "student" ? "/student" : "/"} className="studio-brand" aria-label="Bodha AI home">
          <span className="studio-brand-icon"><Stack size={24} weight="fill" /></span>
          <span>Bodha<span className="studio-accent"> AI</span><small>Question paper studio</small></span>
        </Link>
        <div className="studio-nav-caption">Workspace</div>
        <nav aria-label="Workspace navigation" className="studio-nav">
          {links.map(({ to, label, icon: Icon }) => (
            <NavLink key={to} to={to} end className={({ isActive }) => `studio-nav-link ${isActive ? "is-active" : ""}`}>
              <Icon size={18} /><span>{label}</span>
            </NavLink>
          ))}
        </nav>
        <div className="studio-account">
          <div className="studio-avatar">{(user?.full_name || "B").charAt(0).toUpperCase()}</div>
          <div><strong>{user?.full_name || "Your workspace"}</strong><small>{user?.role || "Teacher"}</small></div>
        </div>
        <button type="button" className="studio-logout" data-testid="logout-button" onClick={() => { logout(); navigate("/login"); }}>
          <SignOut size={17} /> Log out
        </button>
      </aside>
      <div className="studio-workspace">{children}</div>
    </div>
  );
}

export function StudioHeading({ title, description, preview = false }) {
  return (
    <div className="studio-heading no-print">
      <div><h1>{title}</h1><p>{description}</p></div>
      <nav aria-label="Breadcrumb" className="studio-breadcrumb">
        <Link to="/">Dashboard</Link><CaretRight size={12} />
        {preview ? <><Link to="/papers/new">Generate paper</Link><CaretRight size={12} /><span aria-current="page">Preview</span></> : <span aria-current="page">Generate paper</span>}
      </nav>
    </div>
  );
}
