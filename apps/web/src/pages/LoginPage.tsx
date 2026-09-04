import { ArrowRight, Bot, Eye, EyeOff, Layers3, ShieldCheck, Sparkles } from 'lucide-react';
import { useState, type FormEvent } from 'react';
import { Navigate, useLocation, useNavigate } from 'react-router-dom';
import { Brand } from '@/components/brand';
import { useAuth } from '@/components/providers';
import { Button, Field } from '@/components/ui';

export function LoginPage() {
  const { session, login } = useAuth(); const navigate = useNavigate(); const location = useLocation();
  const [email, setEmail] = useState(''); const [password, setPassword] = useState(''); const [showPassword, setShowPassword] = useState(false);
  const [remember, setRemember] = useState(true); const [submitting, setSubmitting] = useState(false); const [error, setError] = useState('');
  if (session) return <Navigate to="/" replace />;
  const submit = async (event: FormEvent) => {
    event.preventDefault(); setError('');
    if (!/^\S+@\S+\.\S+$/.test(email)) { setError('Enter a valid email address.'); return; }
    if (password.length < 6) { setError('Password must be at least 6 characters.'); return; }
    setSubmitting(true);
    try { await login(email, password); const target = (location.state as { from?: string } | null)?.from ?? '/'; navigate(target, { replace: true }); }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'We could not sign you in.'); }
    finally { setSubmitting(false); }
  };
  return <div className="login-page">
    <section className="login-panel">
      <div className="login-panel__inner">
        <Brand />
        <div className="login-heading"><span className="eyebrow-pill"><Sparkles /> AI support, with a compass</span><h1>Welcome back</h1><p>Sign in to train, test, and improve your AI agents.</p></div>
        <form className="login-form" onSubmit={(event) => void submit(event)} noValidate>
          <Field label="Work email" htmlFor="email"><input id="email" type="email" value={email} onChange={(event) => setEmail(event.target.value)} placeholder="you@company.com" autoComplete="email" autoFocus /></Field>
          <Field label="Password" htmlFor="password">
            <div className="password-input"><input id="password" type={showPassword ? 'text' : 'password'} value={password} onChange={(event) => setPassword(event.target.value)} placeholder="Enter your password" autoComplete="current-password" /><button type="button" onClick={() => setShowPassword((value) => !value)} aria-label={showPassword ? 'Hide password' : 'Show password'}>{showPassword ? <EyeOff /> : <Eye />}</button></div>
          </Field>
          <div className="login-options"><label><input type="checkbox" checked={remember} onChange={(event) => setRemember(event.target.checked)} /> Keep me signed in</label><button type="button" className="text-button">Forgot password?</button></div>
          {error ? <div className="form-error" role="alert">{error}</div> : null}
          <Button size="lg" type="submit" disabled={submitting}>{submitting ? <span className="button-spinner" /> : <>Sign in <ArrowRight /></>}</Button>
        </form>
        <p className="signup-copy">New to Northstar? <button className="text-button">Create an account</button></p>
        <p className="login-legal">By continuing, you agree to our <a href="#terms">Terms</a> and <a href="#privacy">Privacy Policy</a>.</p>
      </div>
    </section>
    <section className="login-story" aria-label="Product overview">
      <div className="login-glow login-glow--one" /><div className="login-glow login-glow--two" />
      <div className="story-copy"><span className="story-kicker"><span /> Built for answers you can trust</span><h2>Turn your knowledge into an always-on expert.</h2><p>Deploy a helpful, on-brand AI agent in minutes — and improve every answer with real conversation insights.</p></div>
      <div className="story-window">
        <div className="story-window__bar"><i /><i /><i /><span>Agent performance</span></div>
        <div className="story-window__content"><div className="story-metric"><span>Resolution rate</span><strong>86.7%</strong><small>↑ 4.2% this month</small></div><div className="story-chart"><svg viewBox="0 0 520 150" preserveAspectRatio="none"><defs><linearGradient id="area" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#60e3c2" stopOpacity=".4"/><stop offset="1" stopColor="#60e3c2" stopOpacity="0"/></linearGradient></defs><path d="M0 126 C45 128 55 92 104 100 S170 65 218 82 S287 45 330 59 S397 20 440 40 S485 18 520 10 L520 150 L0 150Z" fill="url(#area)"/><path d="M0 126 C45 128 55 92 104 100 S170 65 218 82 S287 45 330 59 S397 20 440 40 S485 18 520 10" fill="none" stroke="#67e8c6" strokeWidth="4" strokeLinecap="round"/></svg></div></div>
      </div>
      <div className="story-features"><span><Bot /> Purpose-built agents</span><span><Layers3 /> Grounded answers</span><span><ShieldCheck /> Enterprise controls</span></div>
    </section>
  </div>;
}
