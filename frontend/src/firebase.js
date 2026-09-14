import { initializeApp } from "firebase/app";
import { GoogleAuthProvider, getAuth } from "firebase/auth";

// These values are Firebase's public *web app* config — safe to ship to the
// browser (that's how Firebase is designed to work). Actual protection comes
// from the backend verifying ID tokens with the service-account key, which
// stays server-side only. Get these from Firebase Console → Project
// Settings → General → Your apps → SDK setup and configuration.
const firebaseConfig = {
  apiKey: import.meta.env.VITE_FIREBASE_API_KEY,
  authDomain: import.meta.env.VITE_FIREBASE_AUTH_DOMAIN,
  projectId: import.meta.env.VITE_FIREBASE_PROJECT_ID,
  storageBucket: import.meta.env.VITE_FIREBASE_STORAGE_BUCKET,
  messagingSenderId: import.meta.env.VITE_FIREBASE_MESSAGING_SENDER_ID,
  appId: import.meta.env.VITE_FIREBASE_APP_ID,
};

// Degrades to "auth disabled" if no Firebase project is configured yet —
// same pattern as MONGODB_URI/TAVILY_API_KEY on the backend, so the app
// keeps working (without login) rather than crashing on a missing config.
export const authConfigured = Boolean(firebaseConfig.apiKey);

export const auth = authConfigured ? getAuth(initializeApp(firebaseConfig)) : null;
export const googleProvider = authConfigured ? new GoogleAuthProvider() : null;
