import { X, type LucideIcon } from 'lucide-react';
import { useEffect, useId, useRef, type ButtonHTMLAttributes, type ReactNode } from 'react';

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger';
  size?: 'sm' | 'md' | 'lg' | 'icon';
  icon?: LucideIcon;
}

export function Button({ variant = 'primary', size = 'md', icon: Icon, className = '', children, ...props }: ButtonProps) {
  return <button className={`button button--${variant} button--${size} ${className}`} {...props}>{Icon ? <Icon aria-hidden="true" /> : null}{children}</button>;
}

export function Badge({ children, tone = 'neutral' }: { children: ReactNode; tone?: 'neutral' | 'success' | 'warning' | 'danger' | 'brand' | 'purple' }) {
  return <span className={`badge badge--${tone}`}>{children}</span>;
}

export function Card({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <section className={`card ${className}`}>{children}</section>;
}

export function Field({ label, hint, error, children, htmlFor, messageId }: { label: string; hint?: string; error?: string; children: ReactNode; htmlFor?: string; messageId?: string }) {
  return <div className="field"><label htmlFor={htmlFor}>{label}</label>{children}{error ? <small id={messageId} className="field__error">{error}</small> : hint ? <small id={messageId}>{hint}</small> : null}</div>;
}

export function Switch({ checked, onChange, label, description, disabled }: { checked: boolean; onChange: (value: boolean) => void; label: string; description?: string; disabled?: boolean }) {
  return <label className={`switch-row ${disabled ? 'is-disabled' : ''}`}>
    <span><strong>{label}</strong>{description ? <small>{description}</small> : null}</span>
    <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} disabled={disabled} />
    <span className="switch" aria-hidden="true"><span /></span>
  </label>;
}

interface ModalProps { open: boolean; onClose: () => void; title: string; description?: string; children: ReactNode; footer?: ReactNode; size?: 'sm' | 'md' | 'lg'; closeDisabled?: boolean }
export function Modal({ open, onClose, title, description, children, footer, size = 'md', closeDisabled = false }: ModalProps) {
  const titleId = useId();
  const descriptionId = useId();
  const dialogRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef(onClose);
  const closeDisabledRef = useRef(closeDisabled);
  closeRef.current = onClose;
  closeDisabledRef.current = closeDisabled;
  useEffect(() => {
    if (!open) return;
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const focusableSelector = 'button:not(:disabled), [href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])';
    const focusFrame = window.requestAnimationFrame(() => {
      const first = dialogRef.current?.querySelector<HTMLElement>(focusableSelector);
      (first ?? dialogRef.current)?.focus();
    });
    const close = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !closeDisabledRef.current) closeRef.current();
      if (event.key !== 'Tab' || !dialogRef.current) return;
      const focusable = [...dialogRef.current.querySelectorAll<HTMLElement>(focusableSelector)];
      if (!focusable.length) { event.preventDefault(); dialogRef.current.focus(); return; }
      const first = focusable[0]!;
      const last = focusable[focusable.length - 1]!;
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    window.addEventListener('keydown', close); document.body.classList.add('modal-open');
    return () => { window.cancelAnimationFrame(focusFrame); window.removeEventListener('keydown', close); document.body.classList.remove('modal-open'); previousFocus?.focus(); };
  }, [open]);
  if (!open) return null;
  return <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !closeDisabled) onClose(); }}>
    <div ref={dialogRef} tabIndex={-1} className={`modal modal--${size}`} role="dialog" aria-modal="true" aria-labelledby={titleId} aria-describedby={description ? descriptionId : undefined}>
      <div className="modal__header"><div><h2 id={titleId}>{title}</h2>{description ? <p id={descriptionId}>{description}</p> : null}</div><button className="icon-button" onClick={onClose} aria-label="Close dialog" disabled={closeDisabled}><X /></button></div>
      <div className="modal__body">{children}</div>{footer ? <div className="modal__footer">{footer}</div> : null}
    </div>
  </div>;
}

export function Skeleton({ height = 16, width = '100%' }: { height?: number; width?: string }) { return <span className="skeleton" style={{ height, width }} aria-hidden="true" />; }

export function PageLoader() { return <div className="page-loader" aria-label="Loading"><div className="loader-orbit"><span /></div><p>Loading your workspace…</p></div>; }

export function EmptyState({ icon: Icon, title, description, action }: { icon: LucideIcon; title: string; description: string; action?: ReactNode }) {
  return <div className="empty-state"><span className="empty-state__icon"><Icon /></span><h3>{title}</h3><p>{description}</p>{action}</div>;
}

export function StatusDot({ tone = 'success' }: { tone?: 'success' | 'warning' | 'danger' | 'neutral' }) { return <span className={`status-dot status-dot--${tone}`} aria-hidden="true" />; }
