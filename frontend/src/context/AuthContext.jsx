import React, { createContext, useContext, useEffect, useState } from "react";
import {
  GoogleAuthProvider,
  createUserWithEmailAndPassword,
  onAuthStateChanged,
  sendPasswordResetEmail,
  signInWithEmailAndPassword,
  signInWithRedirect,
  signOut,
  updateProfile as updateFirebaseProfile,
} from "firebase/auth";
import { api } from "@/lib/api";
import { firebaseAuth, requireFirebaseAuth } from "@/lib/firebase";

const AuthContext = createContext(null);
const ONBOARDING_KEY = "bodha-profile-onboarding";

const savedOnboardingProfile = () => {
  try {
    return JSON.parse(sessionStorage.getItem(ONBOARDING_KEY) || "null") || {};
  } catch {
    return {};
  }
};

const saveOnboardingProfile = (profile) => {
  sessionStorage.setItem(ONBOARDING_KEY, JSON.stringify(profile));
};

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
    const onboarding = savedOnboardingProfile();
    const response = await api.post("/auth/sync", {
      full_name: firebaseUser.displayName || onboarding.full_name || "",
      school_name: onboarding.school_name || "",
      board: onboarding.board || "",
      board_other_name: onboarding.board_other_name || "",
    });
    if (response.data.profile_complete) sessionStorage.removeItem(ONBOARDING_KEY);
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

  const loginWithGoogle = (profile = null) => {
    if (profile) saveOnboardingProfile(profile);
    return signInWithRedirect(requireFirebaseAuth(), new GoogleAuthProvider());
  };

  const loginWithPassword = async (email, password) => {
    try {
      const credential = await signInWithEmailAndPassword(requireFirebaseAuth(), email, password);
      await syncUser(credential.user);
    } catch (error) {
      throw new Error(authError(error));
    }
  };

  const registerWithPassword = async (fullName, email, password, profile) => {
    try {
      saveOnboardingProfile({ full_name: fullName.trim(), ...profile });
      const credential = await createUserWithEmailAndPassword(requireFirebaseAuth(), email, password);
      await updateFirebaseProfile(credential.user, { displayName: fullName.trim() });
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

  const updateBodhaProfile = async (profile) => {
    const response = await api.patch("/auth/profile", profile);
    setUser(response.data);
    return response.data;
  };

  const hasPasswordProvider = () => firebaseAuth?.currentUser?.providerData?.some((provider) => provider.providerId === "password") || false;

  return (
    <AuthContext.Provider value={{ user, loading, loginWithGoogle, loginWithPassword, registerWithPassword, requestPasswordReset, updateBodhaProfile, hasPasswordProvider, logout }}>
      {children}
    </AuthContext.Provider>
  );
};

export const useAuth = () => {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be inside AuthProvider");
  return ctx;
};
