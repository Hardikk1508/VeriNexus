import { useState } from "react";
import {
  createUserWithEmailAndPassword,
  sendPasswordResetEmail,
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

const FEATURES = [
  { title: "Plan → Research → Verify", body: "Every answer is decomposed into atomic claims before you ever see it." },
  { title: "Nothing shown without evidence", body: "Claims that can't be grounded get hedged, dropped, or flagged — never presented as fact." },
  { title: "Conflicts surfaced, not hidden", body: "When sources disagree, both sides are shown instead of picking a winner." },
];

function IconMail(props) {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" {...props}>
      <rect x="3" y="5" width="18" height="14" rx="2" stroke="currentColor" strokeWidth="1.8" />
      <polyline points="3,7 12,13 21,7" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
function IconLock(props) {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" {...props}>
      <rect x="5" y="11" width="14" height="9" rx="2" stroke="currentColor" strokeWidth="1.8" />
      <path d="M8 11V7a4 4 0 018 0v4" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
    </svg>
  );
}
function IconEye(props) {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" {...props}>
      <path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7-11-7-11-7z" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" />
      <circle cx="12" cy="12" r="3" stroke="currentColor" strokeWidth="1.8" />
    </svg>
  );
}
function IconEyeOff(props) {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" {...props}>
      <path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7-11-7-11-7z" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" />
      <circle cx="12" cy="12" r="3" stroke="currentColor" strokeWidth="1.8" />
      <line x1="2" y1="2" x2="22" y2="22" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
    </svg>
  );
}
function IconCheck(props) {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" {...props}>
      <polyline points="4,13 9,18 20,6" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export default function AuthPage() {
  const [mode, setMode] = useState("signin"); // "signin" | "signup"
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [status, setStatus] = useState("idle"); // idle | busy | error | success
  const [message, setMessage] = useState(""); // error text, or a positive note (e.g. reset email sent)
  const [messageKind, setMessageKind] = useState("error"); // "error" | "info"

  const busy = status === "busy";

  function switchMode(next) {
    setMode(next);
    setStatus("idle");
    setMessage("");
  }

  async function submit(e) {
    e.preventDefault();
    setStatus("busy");
    setMessage("");
    try {
      if (mode === "signup") {
        await createUserWithEmailAndPassword(auth, email, password);
      } else {
        await signInWithEmailAndPassword(auth, email, password);
      }
      setStatus("success"); // App's onAuthStateChanged listener takes it from here
    } catch (err) {
      setStatus("error");
      setMessageKind("error");
      setMessage(friendlyError(err.code));
    }
  }

  async function google() {
    setStatus("busy");
    setMessage("");
    try {
      await signInWithPopup(auth, googleProvider);
      setStatus("success");
    } catch (err) {
      setStatus("error");
      setMessageKind("error");
      setMessage(friendlyError(err.code));
    }
  }

  async function forgotPassword() {
    if (!email) {
      setStatus("error");
      setMessageKind("error");
      setMessage("Enter your email above first, then click this again.");
      return;
    }
    try {
      await sendPasswordResetEmail(auth, email);
      setStatus("idle");
      setMessageKind("info");
      setMessage("Password reset email sent — check your inbox.");
    } catch (err) {
      setStatus("error");
      setMessageKind("error");
      setMessage(friendlyError(err.code));
    }
  }

  const [c1, c2] =
    status === "error" ? ERROR : status === "success" ? SUCCESS : busy ? BUSY : IDLE;

  return (
    <>
      <Backdrop busy={busy || status === "success"} c1={c1} c2={c2} />
      <div className="auth-shell">
        <div className="auth-side">
          <div className="auth-brand">
            <div className="brand-mark">
              <svg width="20" height="20" viewBox="0 0 32 32">
                <polyline points="10,17 14,21 22,11" fill="none" stroke="#fff" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </div>
            <span className="font-display auth-side-title">VeriNexus</span>
          </div>
          <p className="auth-side-tagline">
            Evidence-based research with claim-level verification — every answer
            comes with a confidence score, not just an opinion.
          </p>
          <div className="auth-feature-list">
            {FEATURES.map((f, i) => (
              <div className="auth-feature" key={i} style={{ animationDelay: `${i * 90}ms` }}>
                <span className="auth-feature-icon"><IconCheck /></span>
                <div>
                  <div className="auth-feature-title">{f.title}</div>
                  <div className="auth-feature-body">{f.body}</div>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="auth-main">
          <div className="card auth-card">
            <div className="auth-mobile-brand">
              <div className="brand-mark">
                <svg width="18" height="18" viewBox="0 0 32 32">
                  <polyline points="10,17 14,21 22,11" fill="none" stroke="#fff" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              </div>
              <span className="font-display auth-title">VeriNexus</span>
            </div>

            <div className="auth-tabs">
              <button type="button" className={mode === "signin" ? "active" : ""} onClick={() => switchMode("signin")}>
                Sign in
              </button>
              <button type="button" className={mode === "signup" ? "active" : ""} onClick={() => switchMode("signup")}>
                Create account
              </button>
            </div>

            <form onSubmit={submit} className="auth-form">
              <div className="auth-field">
                <span className="auth-field-icon"><IconMail /></span>
                <input
                  type="email"
                  placeholder="Email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  autoComplete="email"
                  required
                />
              </div>
              <div className="auth-field">
                <span className="auth-field-icon"><IconLock /></span>
                <input
                  type={showPassword ? "text" : "password"}
                  placeholder="Password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  autoComplete={mode === "signup" ? "new-password" : "current-password"}
                  minLength={6}
                  required
                />
                <button
                  type="button"
                  className="auth-field-toggle"
                  onClick={() => setShowPassword((s) => !s)}
                  aria-label={showPassword ? "Hide password" : "Show password"}
                >
                  {showPassword ? <IconEyeOff /> : <IconEye />}
                </button>
              </div>

              {mode === "signin" && (
                <button type="button" className="auth-forgot" onClick={forgotPassword}>
                  Forgot password?
                </button>
              )}

              {message && (
                <div className={`auth-message ${messageKind}`}>{message}</div>
              )}

              <button className="btn-primary auth-submit" type="submit" disabled={busy}>
                {busy ? "Working…" : mode === "signup" ? "Create account" : "Sign in"}
              </button>
            </form>

            <div className="auth-divider"><span>or</span></div>

            <button className="btn-ghost auth-google" onClick={google} disabled={busy} type="button">
              <span className="auth-google-badge">G</span>
              Continue with Google
            </button>
          </div>
        </div>
      </div>
    </>
  );
}
