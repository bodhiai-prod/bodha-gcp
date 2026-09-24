import { initializeApp } from "firebase/app";
import { getAuth } from "firebase/auth";

const firebaseConfig = {
  apiKey: process.env.REACT_APP_FIREBASE_API_KEY,
  authDomain: process.env.REACT_APP_FIREBASE_AUTH_DOMAIN,
  projectId: process.env.REACT_APP_FIREBASE_PROJECT_ID,
  appId: process.env.REACT_APP_FIREBASE_APP_ID,
};

export const firebaseConfigured = Object.values(firebaseConfig).every(Boolean);
export const firebaseAuth = firebaseConfigured ? getAuth(initializeApp(firebaseConfig)) : null;

export function requireFirebaseAuth() {
  if (!firebaseAuth) {
    throw new Error("Firebase Authentication is not configured for this deployment.");
  }
  return firebaseAuth;
}

export async function getFirebaseToken() {
  return firebaseAuth?.currentUser?.getIdToken() || null;
}
