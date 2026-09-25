import { initializeApp } from "firebase/app";
import { getAuth } from "firebase/auth";

const firebaseConfig = {
  apiKey: process.env.REACT_APP_FIREBASE_API_KEY,
  authDomain: process.env.REACT_APP_FIREBASE_AUTH_DOMAIN,
  projectId: process.env.REACT_APP_FIREBASE_PROJECT_ID,
  appId: process.env.REACT_APP_FIREBASE_APP_ID,
};

// `appId` is useful for Firebase products such as Analytics, but Firebase
// Authentication only requires these three options. Keeping it optional also
// makes an auth-only web app resilient if it was registered without an app ID.
const requiredFirebaseOptions = ["apiKey", "authDomain", "projectId"];
const missingFirebaseOptions = requiredFirebaseOptions.filter((key) => !firebaseConfig[key]);
export const firebaseConfigured = missingFirebaseOptions.length === 0;
export const firebaseAuth = firebaseConfigured ? getAuth(initializeApp(firebaseConfig)) : null;

export function requireFirebaseAuth() {
  if (!firebaseAuth) {
    throw new Error(
      `Firebase Authentication is not configured for this deployment. Missing: ${missingFirebaseOptions.join(", ")}.`
    );
  }
  return firebaseAuth;
}

export async function getFirebaseToken() {
  return firebaseAuth?.currentUser?.getIdToken() || null;
}
