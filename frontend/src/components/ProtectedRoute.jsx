import React from "react";
import { Navigate } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";

export default function ProtectedRoute({ children, allowIncomplete = false }) {
  const { user, loading } = useAuth();
  if (loading) return null;
  if (!user) return <Navigate to="/login" replace />;
  if (!allowIncomplete && !user.profile_complete) return <Navigate to="/complete-profile" replace />;
  return children;
}
