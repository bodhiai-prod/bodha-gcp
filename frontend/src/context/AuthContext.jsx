import React, { createContext, useContext, useEffect, useState } from "react";
import { api } from "@/lib/api";

const AuthContext = createContext(null);

export const AuthProvider = ({ children }) => {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // Browser authentication is an HttpOnly cookie; never keep session tokens
    // or a stale user identity in localStorage.
    localStorage.removeItem("qp_token");
    localStorage.removeItem("qp_user");
    api
      .get("/auth/me")
      .then((r) => setUser(r.data))
      .catch(() => setUser(null))
      .finally(() => setLoading(false));
  }, []); // eslint-disable-line

  const loginWithGoogle = () => {
    window.location.assign("/api/auth/google/start");
  };

  const loginWithPassword = async (email, password) => {
    const response = await api.post("/auth/login", { email, password });
    setUser(response.data);
    return response.data;
  };

  const registerWithPassword = async (full_name, email, password) => {
    const response = await api.post("/auth/register", { full_name, email, password });
    setUser(response.data);
    return response.data;
  };

  const logout = () => {
    api.post("/auth/logout").catch(() => {});
    setUser(null);
  };

  return (
    <AuthContext.Provider value={{ user, loading, loginWithGoogle, loginWithPassword, registerWithPassword, logout }}>
      {children}
    </AuthContext.Provider>
  );
};

export const useAuth = () => {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be inside AuthProvider");
  return ctx;
};
