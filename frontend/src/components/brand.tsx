export function BrandMark({ size = 38 }: { size?: number }) {
  return <span className="brand-mark" style={{ width: size, height: size }} aria-hidden="true"><svg viewBox="0 0 64 64"><path d="M32 6.5l5.6 19.9L57.5 32l-19.9 5.6L32 57.5l-5.6-19.9L6.5 32l19.9-5.6L32 6.5z" /><circle cx="32" cy="32" r="4.6" /></svg></span>;
}

export function Brand({ compact = false, light = false }: { compact?: boolean; light?: boolean }) {
  return <div className={`brand ${light ? 'brand--light' : ''}`}><BrandMark />{compact ? null : <span>northstar<span>ai</span></span>}</div>;
}
