import { initializeApp, type FirebaseOptions } from "firebase/app";
import { getFirestore, type Firestore } from "firebase/firestore";

const env = import.meta.env;

const config: FirebaseOptions = {
  apiKey: env.VITE_FIREBASE_API_KEY,
  authDomain: env.VITE_FIREBASE_AUTH_DOMAIN,
  projectId: env.VITE_FIREBASE_PROJECT_ID,
  storageBucket: env.VITE_FIREBASE_STORAGE_BUCKET,
  messagingSenderId: env.VITE_FIREBASE_MESSAGING_SENDER_ID,
  appId: env.VITE_FIREBASE_APP_ID,
};

export const firebaseConfigured = Boolean(config.apiKey && config.projectId && config.appId);

// Read-only use: the dashboard never writes (Firestore rules deny all client writes).
export const db: Firestore | null = firebaseConfigured ? getFirestore(initializeApp(config)) : null;

export const REPO_URL = "https://github.com/Aaditya-Nepal00/chesterton";
