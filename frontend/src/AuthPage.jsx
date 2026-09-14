import { useState } from "react";
import {
  createUserWithEmailAndPassword,
  signInWithEmailAndPassword,
  signInWithPopup,
} from "firebase/auth";
import { auth, googleProvider } from "./firebase";
import Backdrop from "./Backdrop";

const IDLE = ["#4db2e8", "#7c3aed"];
const BUSY = ["#d97706", "#fbbf24"];
const ERROR = ["#f87171", "#dd5b57"];
const SUCCESS = ["#34d399", "#22c55e"];

const ERROR_MESSAGES = {
  "auth/invalid-email": "That email address doesn't look right.",
  "auth/user-not-found": "No account with that email.",
  "auth/wrong-password": "Incorrect password.",
  "auth/invalid-credential": "Incorrect email or password.",
  "auth/email-already-in-use": "An account already exists for that email.",
  "auth/weak-password": "Password should be at least 6 characters.",
  "auth/popup-closed-by-user": "Sign-in was cancelled.",
};

function friendlyError(code) {
  return ERROR_MESSAGES[code] || "Something went wrong. Please try again.";
}

export default function AuthPage() {
  const [mode, setMode] = useState("signin"); // "signin" | "signup"
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [status, setStatus] = useState("idle"); // idle | busy | error | success
  const [error, setError] = useState("");

  const busy = status === "busy";

  async function submit(e) {
    e.preventDefault();
    setStatus("busy");
    setError("");
    try {
      if (mode === "signup") {
        await createUserWithEmailAndPassword(auth, email, password);
      } else {
        await signInWithEmailAndPassword(auth, email, password);
      }
      setStatus("success"); // App's onAuthStateChanged listener takes it from here
    } catch (err) {
      setStatus("error");
      setError(friendlyError(err.code));
    }
  }

  async function google() {
    setStatus("busy");
    setError("");
    try {
      await signInWithPopup(auth, googleProvider);
      setStatus("success");
    } catch (err) {
      setStatus("error");
      setError(friendlyError(err.code));
    }
  }

  const [c1, c2] =
    status === "error" ? ERROR : status === "success" ? SUCCESS : busy ? BUSY : IDLE;

  return (
    <>
      <Backdrop busy={busy || status === "success"} c1={c1} c2={c2} />
      <div className="auth-page">
        <div className="card auth-card">
          <div className="auth-brand">
            <div className="brand-mark">
              <svg width="20" height="20" viewBox="0 0 32 32">
                <polyline points="10,17 14,21 22,11" fill="none" stroke="#fff" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </div>
            <span className="font-display auth-title">VeriNexus</span>
          </div>
          <p className="auth-subtitle">Evidence-based research with claim-level verification.</p>

          <div className="auth-tabs">
            <button
              type="button"
              className={mode === "signin" ? "active" : ""}
              onClick={() => { setMode("signin"); setStatus("idle"); setError(""); }}
            >
              Sign in
            </button>
            <button
              type="button"
              className={mode === "signup" ? "active" : ""}
              onClick={() => { setMode("signup"); setStatus("idle"); setError(""); }}
            >
              Create account
            </button>
          </div>

          <form onSubmit={submit} className="auth-form">
            <input
              type="email"
              placeholder="Email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="email"
              required
            />
            <input
              type="password"
              placeholder="Password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete={mode === "signup" ? "new-password" : "current-password"}
              minLength={6}
              required
            />
            {error && <div className="auth-error">{error}</div>}
            <button className="btn-primary auth-submit" type="submit" disabled={busy}>
              {busy ? "Working…" : mode === "signup" ? "Create account" : "Sign in"}
            </button>
          </form>

          <div className="auth-divider"><span>or</span></div>

          <button className="btn-ghost auth-google" onClick={google} disabled={busy} type="button">
            Continue with Google
          </button>
        </div>
      </div>
    </>
  );
}
