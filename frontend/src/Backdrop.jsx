const PARTICLES = Array.from({ length: 22 }, () => ({
  left: Math.random() * 100,
  delay: Math.random() * 16,
  duration: 10 + Math.random() * 10,
  size: 2 + Math.random() * 2,
}));

/** Full-screen fixed 3D scene: perspective grid plane + drifting glow blobs + rising
 *  particles. Always animating; purely presentational — callers drive its color/speed
 *  (used on both the research page and the auth page, so the whole app feels like one
 *  live system rather than each screen having its own decoration). */
export default function Backdrop({ busy, c1 = "#4db2e8", c2 = "#7c3aed" }) {
  return (
    <div className={`backdrop${busy ? " busy" : ""}`} style={{ "--bd-color": c1, "--bd-color-2": c2 }} aria-hidden="true">
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
