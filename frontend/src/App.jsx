import { useEffect, useRef, useState } from "react";
import { getHealth, streamResearch, uploadDocument } from "./api";

const BANDS = {
  VERIFIED: { fg: "#16794a", bg: "var(--verified-bg)", grad: "linear-gradient(135deg,#22a35f,#15803d)", glow: "rgba(21,128,61,0.35)" },
  PARTIAL: { fg: "#8a5a08", bg: "var(--partial-bg)", grad: "linear-gradient(135deg,#d99a2b,#8a5a08)", glow: "rgba(138,90,8,0.3)" },
  WEAK: { fg: "#b26a00", bg: "var(--weak-bg)", grad: "linear-gradient(135deg,#e8963a,#b26a00)", glow: "rgba(178,106,0,0.3)" },
  UNSUPPORTED: { fg: "#9b2c2c", bg: "var(--unsupported-bg)", grad: "linear-gradient(135deg,#dd5b57,#9b2c2c)", glow: "rgba(155,44,44,0.3)" },
};
const CONFLICT_STYLE = { fg: "#6d28d9", bg: "var(--conflict-bg)", grad: "linear-gradient(135deg,#9463e6,#6d28d9)", glow: "rgba(109,40,217,0.32)" };

const PHASE_COLORS = {
  idle: ["#1177b8", "#7c3aed"],
  plan: ["#1177b8", "#0ea5e9"],
  research: ["#7c3aed", "#a855f7"],
  draft: ["#4f46e5", "#818cf8"],
  verify: ["#d97706", "#fbbf24"],
  finalise: ["#0d9488", "#2dd4bf"],
};

function hexToRgba(hex, alpha) {
  const h = hex.replace("#", "");
  const full = h.length === 3 ? h.split("").map((c) => c + c).join("") : h;
  const n = parseInt(full, 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${alpha})`;
}

function phaseFromStages(stages) {
  if (!stages.length) return "plan";
  const key = stages[stages.length - 1].stage || stages[stages.length - 1].name;
  if (key === "verify") return "verify";
  if (key === "research" || key === "repair") return "research";
  if (key === "draft" || key === "decompose") return "draft";
  if (key === "finalise") return "finalise";
  return "plan";
}

const PARTICLES = Array.from({ length: 22 }, () => ({
  left: Math.random() * 100,
  delay: Math.random() * 16,
  duration: 10 + Math.random() * 10,
  size: 2 + Math.random() * 2,
}));

/** Full-screen fixed 3D scene: perspective grid plane + drifting glow blobs + rising
 *  particles. Always animating; color and speed react to the same pipeline state as
 *  the status orb, so the whole page feels like one live system, not decoration. */
function Backdrop({ running, stages, result }) {
  let c1, c2;
  if (result) {
    const s = bandStyle(result.band, result.conflicted);
    c1 = s.fg;
    c2 = s.fg;
  } else if (running) {
    [c1, c2] = PHASE_COLORS[phaseFromStages(stages)] || PHASE_COLORS.idle;
  } else {
    [c1, c2] = PHASE_COLORS.idle;
  }
  return (
    <div className={`backdrop${running ? " busy" : ""}`} style={{ "--bd-color": c1, "--bd-color-2": c2 }} aria-hidden="true">
      <div className="backdrop-grid-wrap"><div className="backdrop-grid" /></div>
      <div className="backdrop-glow g1" />
      <div className="backdrop-glow g2" />
      <div className="backdrop-glow g3" />
      <div className="backdrop-particles">
        {PARTICLES.map((p, i) => (
          <span
            key={i}
            className="backdrop-particle"
            style={{
              left: `${p.left}%`,
              bottom: "-10px",
              width: p.size,
              height: p.size,
              animationDuration: `${p.duration}s`,
              animationDelay: `${-p.delay}s`,
            }}
          />
        ))}
      </div>
    </div>
  );
}

/** Persistent 3D orbit — idles forever, speeds up while running, settles to the verdict color when done. */
function StatusOrb({ running, stages, result }) {
  let c1, c2;
  if (result) {
    const s = bandStyle(result.band, result.conflicted);
    c1 = s.fg;
    c2 = s.fg;
  } else if (running) {
    [c1, c2] = PHASE_COLORS[phaseFromStages(stages)] || PHASE_COLORS.idle;
  } else {
    [c1, c2] = PHASE_COLORS.idle;
  }
  return (
    <div
      className={`orb-wrap${running ? " busy" : ""}`}
      style={{ "--orb-color": c1, "--orb-color-2": c2, "--orb-glow": hexToRgba(c1, 0.5) }}
    >
      <div className="orb-tilt">
        <div className="orb-ring r2" />
        <div className="orb-ring r1" />
        <div className="orb-ring r3" />
        <div className="orb-core" />
      </div>
    </div>
  );
}

const EXAMPLE_QUESTIONS = [
  "Does retrieval-augmented generation actually reduce hallucination?",
  "Is intermittent fasting backed by strong clinical evidence?",
  "Are electric vehicles really better for the environment than combustion cars?",
  "Does remote work reduce team productivity?",
];

/* ------------------------------------------------------------------ icons */

const iconProps = { width: 15, height: 15, viewBox: "0 0 24 24", fill: "none" };

function IconCheck(props) {
  return (
    <svg {...iconProps} {...props}>
      <polyline points="4,13 9,18 20,6" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
function IconArrow(props) {
  return (
    <svg {...iconProps} {...props}>
      <line x1="4" y1="12" x2="18" y2="12" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" />
      <polyline points="13,7 18,12 13,17" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
function IconFilePlus(props) {
  return (
    <svg {...iconProps} {...props}>
      <path d="M6 2h9l5 5v15H6z" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />
      <path d="M15 2v5h5" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />
      <line x1="9" y1="15" x2="15" y2="15" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
      <line x1="12" y1="12" x2="12" y2="18" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  );
}
function IconFile(props) {
  return (
    <svg {...iconProps} {...props}>
      <path d="M6 2h9l5 5v15H6z" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />
      <path d="M15 2v5h5" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />
    </svg>
  );
}
function IconStop(props) {
  return (
    <svg {...iconProps} {...props}>
      <rect x="6" y="6" width="12" height="12" rx="2" stroke="currentColor" strokeWidth="2" />
    </svg>
  );
}
function IconExternal(props) {
  return (
    <svg {...iconProps} width={11} height={11} {...props}>
      <path d="M14 4h6v6" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
      <line x1="20" y1="4" x2="10" y2="14" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
      <path d="M18 14v6H4V6h6" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
function IconChevron(props) {
  return (
    <svg {...iconProps} width={11} height={11} {...props}>
      <polyline points="6,9 12,15 18,9" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
function IconSpinner(props) {
  return (
    <svg {...iconProps} width={13} height={13} className="spin" {...props}>
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="2.4" strokeOpacity="0.25" />
      <path d="M21 12a9 9 0 00-9-9" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" />
    </svg>
  );
}

/* ------------------------------------------------------------------ small components */

function bandStyle(band, conflicted) {
  return conflicted ? CONFLICT_STYLE : BANDS[band] || BANDS.UNSUPPORTED;
}

function Chip({ band, conflicted }) {
  const s = bandStyle(band, conflicted);
  return (
    <span className="chip-grad" style={{ background: s.grad, boxShadow: `0 3px 10px ${s.glow}` }}>
      {conflicted ? "CONFLICT" : band}
    </span>
  );
}

/** Tiny hover 3D tilt — CSS-only transform driven by mouse position, no library. */
function Tilt({ children, className = "", max = 6, style = {} }) {
  const ref = useRef(null);
  const [vars, setVars] = useState({});
  function onMove(e) {
    const el = ref.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const px = (e.clientX - rect.left) / rect.width;
    const py = (e.clientY - rect.top) / rect.height;
    setVars({ "--ry": `${(px - 0.5) * max * 2}deg`, "--rx": `${(0.5 - py) * max * 2}deg` });
  }
  function onLeave() {
    setVars({ "--ry": "0deg", "--rx": "0deg" });
  }
  return (
    <div
      ref={ref}
      className={`tilt ${className}`}
      style={{ ...vars, ...style }}
      onMouseMove={onMove}
      onMouseLeave={onLeave}
    >
      {children}
    </div>
  );
}

function CountUp({ value, decimals = 0 }) {
  const [display, setDisplay] = useState(0);
  useEffect(() => {
    let raf;
    const start = performance.now();
    const dur = 700;
    function tick(t) {
      const p = Math.min(1, (t - start) / dur);
      const eased = 1 - Math.pow(1 - p, 3);
      setDisplay(value * eased);
      if (p < 1) raf = requestAnimationFrame(tick);
    }
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [value]);
  return <>{display.toFixed(decimals)}</>;
}

/** 4-axis radar plot for a claim's support / agreement / authority / contradiction signals. */
function SignalRadar({ signals = {}, color = "#1177b8" }) {
  const support = signals.support ?? 0;
  const agreement = signals.agreement ?? 0;
  const authority = signals.authority ?? 0;
  const contradiction = signals.contradiction ?? 0;
  const cx = 55, cy = 55, maxR = 40;
  const angles = [-90, 0, 90, 180]; // support(top), agreement(right), authority(bottom), contradiction(left)
  const values = [support, agreement, authority, contradiction];

  const pt = (angleDeg, v) => {
    const rad = (angleDeg * Math.PI) / 180;
    return [cx + v * maxR * Math.cos(rad), cy + v * maxR * Math.sin(rad)];
  };
  const poly = (v) => angles.map((a, i) => pt(a, v[i] ?? v).join(",")).join(" ");

  return (
    <svg width="110" height="110" viewBox="0 0 110 110">
      <polygon points={poly(1)} fill="none" stroke="var(--line)" strokeWidth="1" />
      <polygon points={poly(0.5)} fill="none" stroke="var(--line)" strokeWidth="1" />
      {angles.map((a, i) => {
        const [x, y] = pt(a, 1);
        return <line key={i} x1={cx} y1={cy} x2={x} y2={y} stroke="var(--line)" strokeWidth="1" />;
      })}
      <polygon points={poly(values)} fill={color} fillOpacity="0.22" stroke={color} strokeWidth="2" strokeLinejoin="round" />
      {angles.map((a, i) => {
        const [x, y] = pt(a, values[i]);
        return <circle key={i} cx={x} cy={y} r="2.5" fill={color} />;
      })}
    </svg>
  );
}

function ConfidenceGauge({ value, band, conflicted }) {
  const c = BANDS[band] || BANDS.UNSUPPORTED;
  const color = conflicted ? "var(--conflict)" : c.fg;
  const pct = Math.round((value || 0) * 100);
  const [display, setDisplay] = useState(0);

  useEffect(() => {
    let raf;
    const start = performance.now();
    const dur = 900;
    function tick(t) {
      const p = Math.min(1, (t - start) / dur);
      const eased = 1 - Math.pow(1 - p, 3);
      setDisplay(Math.round(pct * eased));
      if (p < 1) raf = requestAnimationFrame(tick);
    }
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [pct]);

  const r = 27;
  const C = 2 * Math.PI * r;
  const offset = C * (1 - pct / 100);

  return (
    <div className="gauge">
      <svg width="68" height="68" viewBox="0 0 68 68">
        <circle className="gauge-ring-bg" cx="34" cy="34" r={r} fill="none" strokeWidth="6" />
        <circle
          className="gauge-ring-fg"
          cx="34"
          cy="34"
          r={r}
          fill="none"
          stroke={color}
          strokeWidth="6"
          strokeLinecap="round"
          strokeDasharray={C}
          strokeDashoffset={offset}
          transform="rotate(-90 34 34)"
        />
      </svg>
      <div className="gauge-value" style={{ color }}>{display}%</div>
    </div>
  );
}

function ClaimRow({ claim, index }) {
  const [open, setOpen] = useState(false);
  const sig = claim.signals || {};
  const s = bandStyle(claim.band, claim.conflicted);

  return (
    <div
      className="claim-row"
      style={{ animationDelay: `${Math.min(index, 8) * 55}ms`, "--claim-color": s.fg }}
    >
      <div className="claim-head">
        <Chip band={claim.band} conflicted={claim.conflicted} />
        <div className="claim-text">
          <div>{claim.text}</div>
          <button className={`why-btn${open ? " open" : ""}`} onClick={() => setOpen(!open)}>
            {open ? "hide evidence" : `why? · ${(claim.evidence || []).length} sources`}
            <IconChevron className="chevron" />
          </button>
        </div>
        <div className="claim-conf">{claim.confidence?.toFixed?.(2) ?? claim.confidence}</div>
      </div>

      {open && (
        <div className="evidence-wrap">
          <div className="radar-block">
            <SignalRadar signals={sig} color={s.fg} />
            <div className="radar-legend">
              <span><span className="radar-legend-dot" style={{ background: s.fg }} />support <b>{sig.support}</b></span>
              <span><span className="radar-legend-dot" style={{ background: s.fg, opacity: 0.75 }} />agreement <b>{sig.agreement}</b></span>
              <span><span className="radar-legend-dot" style={{ background: s.fg, opacity: 0.5 }} />authority <b>{sig.authority}</b></span>
              <span><span className="radar-legend-dot" style={{ background: "var(--unsupported)" }} />contradiction <b>{sig.contradiction}</b></span>
            </div>
          </div>
          {(claim.evidence || []).map((e) => (
            <Tilt key={e.chunk_id} className="evidence-card" max={3}>
              <div className="evidence-head">
                <span className="evidence-domain">{e.domain}</span>
                <span className="evidence-meta">
                  {e.label} · entail {e.p_entail} · contra {e.p_contradict} · auth {e.authority}
                </span>
              </div>
              <div className="evidence-snippet">{e.snippet}</div>
            </Tilt>
          ))}
        </div>
      )}
    </div>
  );
}

function StatStrip({ claims, sources, resultConfidence }) {
  if (claims.length === 0 && sources.length === 0) return null;
  const avgConf = claims.length
    ? claims.reduce((a, c) => a + (c.confidence || 0), 0) / claims.length
    : resultConfidence ?? 0;
  const verifiedCount = claims.filter((c) => c.band === "VERIFIED").length;

  const tiles = [
    { cls: "s-sources", value: sources.length, decimals: 0, caption: "Sources found" },
    { cls: "s-claims", value: claims.length, decimals: 0, caption: "Claims checked" },
    { cls: "s-confidence", value: avgConf * 100, decimals: 0, caption: "Avg. confidence", suffix: "%" },
    { cls: "s-verified", value: verifiedCount, decimals: 0, caption: "Fully verified" },
  ];

  return (
    <div className="stat-strip">
      {tiles.map((t, i) => (
        <Tilt key={i} className={`stat-tile ${t.cls}`} max={5}>
          <div className="stat-value"><CountUp value={t.value} decimals={t.decimals} />{t.suffix || ""}</div>
          <div className="stat-caption">{t.caption}</div>
        </Tilt>
      ))}
    </div>
  );
}

function StageTimeline({ stages, running, tick }) {
  if (stages.length === 0 && !running) {
    return <div className="idle-note">Idle — ask a question to start the pipeline.</div>;
  }
  const lastAt = stages.length ? stages[stages.length - 1].at : null;
  const elapsed = running && lastAt ? Math.max(0, tick - lastAt) : 0;

  return (
    <div className="timeline">
      {stages.map((s, i) => (
        <div className="stage-item" key={i}>
          <span className="stage-dot done"><IconCheck /></span>
          <div className="stage-label-row">
            <span>{s.label || s.stage || s.name}</span>
            <span className="stage-time">{s.duration_ms ? `${s.duration_ms} ms` : ""}</span>
          </div>
        </div>
      ))}
      {running && (
        <div className="stage-item">
          <span className="stage-dot active"><span className="ring" /></span>
          <div className="stage-label-row">
            <span className="stage-active-label">Working…</span>
            <span className="stage-time">{(elapsed / 1000).toFixed(1)}s</span>
          </div>
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ app */

export default function App() {
  const [health, setHealth] = useState(null);
  const [query, setQuery] = useState("");
  const [stages, setStages] = useState([]);
  const [result, setResult] = useState(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const [sessionId] = useState(() => crypto.randomUUID());
  const [attachments, setAttachments] = useState([]);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState("");
  const [phIdx, setPhIdx] = useState(0);
  const [tick, setTick] = useState(Date.now());
  const [liveClaims, setLiveClaims] = useState([]);
  const [liveSources, setLiveSources] = useState([]);
  const abortRef = useRef(null);
  const fileInputRef = useRef(null);

  useEffect(() => {
    getHealth().then(setHealth).catch(() => setHealth({ status: "unreachable" }));
  }, []);

  // rotate example placeholder while the box is empty
  useEffect(() => {
    if (query) return;
    const id = setInterval(() => setPhIdx((i) => (i + 1) % EXAMPLE_QUESTIONS.length), 3400);
    return () => clearInterval(id);
  }, [query]);

  // live-tick the "working…" elapsed timer while a run is in flight
  useEffect(() => {
    if (!running) return;
    const id = setInterval(() => setTick(Date.now()), 200);
    return () => clearInterval(id);
  }, [running]);

  async function handleFileChange(e) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setUploading(true);
    setUploadError("");
    try {
      await uploadDocument(sessionId, file);
      setAttachments((a) => [...a, file.name]);
    } catch {
      setUploadError(`Couldn't attach "${file.name}" — only PDF files are supported.`);
    } finally {
      setUploading(false);
    }
  }

  function run() {
    if (query.trim().length < 8 || running) return;
    setRunning(true);
    setStages([]);
    setResult(null);
    setError("");
    setTick(Date.now());
    setLiveClaims([]);
    setLiveSources([]);

    abortRef.current = streamResearch(query, {
      sessionId,
      onStage: (name, payload) => {
        setStages((s) => [...s, { name, ...payload, at: Date.now() }]);
        // These stage payloads carry real intermediate pipeline state — render
        // it as it arrives instead of waiting for the final "done" event.
        if (payload.claims) setLiveClaims(payload.claims);
        if (payload.sources) setLiveSources(payload.sources);
      },
      onDone: (r) => {
        setResult(r);
        setRunning(false);
      },
      onError: () => {
        setError("Stream failed. Check that the backend is running and GROQ_API_KEY is set.");
        setRunning(false);
      },
    });
  }

  function stop() {
    abortRef.current?.();
    setRunning(false);
  }

  const missing = health?.missing_config || [];
  const apiOk = health?.status === "ok";
  const sourcesToShow = result?.sources?.length ? result.sources : liveSources;

  return (
    <>
      <Backdrop running={running} stages={stages} result={result} />
      <div className="page">
      <div className="header-row">
        <div className="brand-mark">
          <svg width="20" height="20" viewBox="0 0 32 32">
            <polyline points="10,17 14,21 22,11" fill="none" stroke="#fff" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </div>
        <div>
          <h1 className="h1 font-display">VeriNexus</h1>
          <p className="sub">
            Evidence-based research with claim-level verification.
            {health && (
              <span className="status-pill">
                <span className={`pulse-dot${apiOk ? "" : " off"}`} />
                {apiOk ? "API online" : "API unreachable"}
                {health.model ? ` · ${health.model}` : ""}
                {health.nli_backend ? ` · NLI: ${health.nli_backend}` : ""}
              </span>
            )}
          </p>
        </div>
        <StatusOrb running={running} stages={stages} result={result} />
      </div>

      {missing.length > 0 && (
        <div className="banner">
          <strong>Running degraded.</strong>&nbsp;Not configured: {missing.join(", ")}.
        </div>
      )}

      <div className="card query-card">
        <textarea
          className="query-input"
          placeholder={`Ask a research question — e.g. ${EXAMPLE_QUESTIONS[phIdx]}`}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) run();
          }}
        />
        <div className="query-actions">
          <button className="btn-primary" onClick={run} disabled={running || query.trim().length < 8}>
            {running ? <IconSpinner /> : null}
            {running ? "Researching…" : "Research"}
            {!running && <span className="arrow"><IconArrow /></span>}
          </button>
          {running && (
            <button className="btn-ghost" onClick={stop}>
              <IconStop /> Stop
            </button>
          )}
          <input
            ref={fileInputRef}
            type="file"
            accept="application/pdf"
            onChange={handleFileChange}
            style={{ display: "none" }}
          />
          <button className="btn-ghost" onClick={() => fileInputRef.current?.click()} disabled={uploading}>
            {uploading ? <IconSpinner /> : <IconFilePlus />}
            {uploading ? "Attaching…" : "Attach PDF"}
          </button>
          <span className="hint">⌘/Ctrl + Enter</span>
        </div>

        {attachments.length > 0 && (
          <div className="attachment-row">
            {attachments.map((name, i) => (
              <span className="attachment-chip" key={i}>
                <IconFile width={12} height={12} /> {name}
              </span>
            ))}
          </div>
        )}
        {uploadError && <div className="banner error" style={{ marginTop: 12, marginBottom: 0 }}>{uploadError}</div>}
      </div>

      {error && <div className="banner error">{error}</div>}

      <StatStrip
        claims={result?.claims?.length ? result.claims : liveClaims}
        sources={sourcesToShow}
        resultConfidence={result?.confidence}
      />

      <div className="grid">
        <div>
          {result ? (
            <div className="card">
              <div className="result-head">
                <ConfidenceGauge value={result.confidence} band={result.band} conflicted={result.conflicted} />
                <div>
                  <Chip band={result.band} />
                  {result.needs_human_review && (
                    <div className="review-flag" style={{ marginTop: 6 }}>flagged for human review</div>
                  )}
                </div>
              </div>

              <div className="answer-text">{result.answer}</div>

              {result.flags?.length > 0 && (
                <div className="flags-row">
                  {result.flags.map((f, i) => <span key={i}>{f}</span>)}
                </div>
              )}

              <div className="label">Claims ({result.claims?.length || 0})</div>
              {(result.claims || []).map((c, i) => (
                <ClaimRow key={c.claim_id ?? i} claim={c} index={i} />
              ))}
            </div>
          ) : running && liveClaims.length > 0 ? (
            <div className="card">
              <div className="live-label">
                <IconSpinner width={13} height={13} />
                Verifying live — {liveClaims.length} claim{liveClaims.length === 1 ? "" : "s"} so far, subject to change
              </div>
              {liveClaims.map((c, i) => (
                <ClaimRow key={c.claim_id ?? i} claim={c} index={i} />
              ))}
            </div>
          ) : (
            <div className="card empty-state">
              {running ? (
                <>
                  <IconSpinner width={16} height={16} />
                  {liveSources.length > 0
                    ? `Drafting from ${liveSources.length} sources — claims will appear here as they're checked.`
                    : "Planning and retrieving — stages appear on the right."}
                </>
              ) : (
                "Results will appear here."
              )}
            </div>
          )}
        </div>

        <div className="card">
          <div className="label" style={{ marginBottom: 14 }}>Pipeline</div>
          <StageTimeline stages={stages} running={running} tick={tick} />

          {sourcesToShow.length > 0 && (
            <>
              <div className="label" style={{ margin: "22px 0 12px" }}>
                Sources ({sourcesToShow.length}){running ? " · live" : ""}
              </div>
              {sourcesToShow.map((src) => (
                <Tilt key={src.source_id} max={3}>
                  <a href={src.url} target="_blank" rel="noreferrer" className="source-link">
                    <div className="source-title">
                      {src.title?.slice(0, 58) || src.domain}
                      <IconExternal />
                    </div>
                    <div className="source-meta">{src.domain} · authority {src.authority}</div>
                    <div className="authority-bar">
                      <div className="authority-bar-fill" style={{ width: `${(src.authority || 0) * 100}%` }} />
                    </div>
                  </a>
                </Tilt>
              ))}
            </>
          )}
        </div>
      </div>
      </div>
    </>
  );
}
