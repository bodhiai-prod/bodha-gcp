import React, { createContext, useContext, useEffect, useState } from "react";
import {
  GoogleAuthProvider,
  createUserWithEmailAndPassword,
  onAuthStateChanged,
  sendPasswordResetEmail,
  signInWithEmailAndPassword,
  signInWithRedirect,
  signOut,
  updateProfile,
} from "firebase/auth";
import { api } from "@/lib/api";
import { firebaseAuth, requireFirebaseAuth } from "@/lib/firebase";

const AuthContext = createContext(null);

function authError(error) {
  const codes = {
    "auth/email-already-in-use": "An account already exists for this email. Sign in instead.",
    "auth/invalid-credential": "Incorrect email or password.",
    "auth/weak-password": "Choose a stronger password.",
    "auth/too-many-requests": "Too many attempts. Please try again later.",
  };
  return codes[error?.code] || error?.message || "Authentication could not be completed.";
}

export const AuthProvider = ({ children }) => {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  const syncUser = async (firebaseUser) => {
    const response = await api.post("/auth/sync", { full_name: firebaseUser.displayName || "" });
    setUser(response.data);
    return response.data;
  };

  useEffect(() => {
    if (!firebaseAuth) {
      setLoading(false);
      return undefined;
    }
    const unsubscribe = onAuthStateChanged(firebaseAuth, async (firebaseUser) => {
      if (!firebaseUser) {
        setUser(null);
        setLoading(false);
        return;
      }
      try {
        await syncUser(firebaseUser);
      } catch {
        setUser(null);
      } finally {
        setLoading(false);
      }
    });
    return unsubscribe;
  }, []);

  const loginWithGoogle = () => signInWithRedirect(requireFirebaseAuth(), new GoogleAuthProvider());

  const loginWithPassword = async (email, password) => {
    try {
      const credential = await signInWithEmailAndPassword(requireFirebaseAuth(), email, password);
      await syncUser(credential.user);
    } catch (error) {
      throw new Error(authError(error));
    }
  };

  const registerWithPassword = async (fullName, email, password) => {
    try {
      const credential = await createUserWithEmailAndPassword(requireFirebaseAuth(), email, password);
      await updateProfile(credential.user, { displayName: fullName.trim() });
      await syncUser(credential.user);
    } catch (error) {
      throw new Error(authError(error));
    }
  };

  const requestPasswordReset = async (email) => {
    try {
      await sendPasswordResetEmail(requireFirebaseAuth(), email, {
        url: `${window.location.origin}/reset-password`,
        handleCodeInApp: true,
      });
    } catch (error) {
      // The UI intentionally returns the same confirmation for all outcomes.
      if (error?.code === "auth/invalid-email") throw new Error("Enter a valid email address.");
    }
  };

  const logout = async () => {
    if (firebaseAuth) await signOut(firebaseAuth);
    setUser(null);
  };

  return (
    <AuthContext.Provider value={{ user, loading, loginWithGoogle, loginWithPassword, registerWithPassword, requestPasswordReset, logout }}>
      {children}
    </AuthContext.Provider>
  );
};

export const useAuth = () => {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be inside AuthProvider");
  return ctx;
};
