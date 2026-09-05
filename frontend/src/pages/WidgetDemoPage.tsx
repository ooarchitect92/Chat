import { ArrowLeft, CheckCircle2, Code2, ShieldCheck, Sparkles } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { Brand } from '@/components/brand';
import { ChatWidget } from '@/components/chat-widget';
import { Badge, PageLoader } from '@/components/ui';
import { agentFromWidgetBootstrap, api } from '@/lib/api';
import { useApi } from '@/lib/use-api';
import type {
  WidgetInitMessage, WidgetReadyMessage, WidgetSession, WidgetSessionErrorMessage,
  WidgetSessionMessage, WidgetSessionRequestMessage,
} from '@/types';

function isWidgetInitMessage(value: unknown, publicId: string): value is WidgetInitMessage {
  if (!value || typeof value !== 'object') return false;
  const message = value as Partial<WidgetInitMessage>;
  return message.type === 'northstar:init'
    && message.agentId === publicId
    && message.bootstrap?.publicId === publicId
    && typeof message.bootstrap.agentId === 'string'
    && typeof message.bootstrap.appearance === 'object'
    && typeof message.session?.conversationId === 'string'
    && typeof message.session.sessionToken === 'string'
    && typeof message.session.expiresAt === 'string';
}

function isWidgetSessionMessage(value: unknown, publicId: string): value is WidgetSessionMessage {
  if (!value || typeof value !== 'object') return false;
  const message = value as Partial<WidgetSessionMessage>;
  return message.type === 'northstar:session'
    && message.agentId === publicId
    && typeof message.requestId === 'string'
    && typeof message.session?.conversationId === 'string'
    && typeof message.session.sessionToken === 'string'
    && typeof message.session.expiresAt === 'string';
}

function isWidgetSessionErrorMessage(value: unknown, publicId: string): value is WidgetSessionErrorMessage {
  if (!value || typeof value !== 'object') return false;
  const message = value as Partial<WidgetSessionErrorMessage>;
  return message.type === 'northstar:session-error'
    && message.agentId === publicId
    && typeof message.requestId === 'string'
    && typeof message.message === 'string';
}

export function WidgetDemoPage({ embedded = false }: { embedded?: boolean }) {
  return embedded ? <EmbeddedWidgetPage /> : <HostedWidgetPage />;
}

function EmbeddedWidgetPage() {
  const { agentId = 'northstar-guide' } = useParams();
  const [initialization, setInitialization] = useState<WidgetInitMessage>();
  const pendingSessions = useRef(new Map<string, { resolve: (session: WidgetSession) => void; reject: (error: Error) => void; timeout: number }>());

  const requestNewSession = useCallback(() => new Promise<WidgetSession>((resolve, reject) => {
    const requestId = crypto.randomUUID?.() ?? `session-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    const timeout = window.setTimeout(() => {
      pendingSessions.current.delete(requestId);
      reject(new Error('Timed out while starting a new conversation.'));
    }, 15_000);
    pendingSessions.current.set(requestId, { resolve, reject, timeout });
    const request: WidgetSessionRequestMessage = { type: 'northstar:session-request', agentId, requestId };
    window.parent.postMessage(request, '*');
  }), [agentId]);

  useEffect(() => {
    let initialized = false;
    const sessions = pendingSessions.current;
    const receive = (event: MessageEvent<unknown>) => {
      if (event.source !== window.parent) return;
      if (!initialized && isWidgetInitMessage(event.data, agentId)) {
        initialized = true;
        setInitialization(event.data);
        return;
      }
      if (isWidgetSessionMessage(event.data, agentId)) {
        const pending = sessions.get(event.data.requestId);
        if (!pending) return;
        window.clearTimeout(pending.timeout);
        sessions.delete(event.data.requestId);
        pending.resolve(event.data.session);
        return;
      }
      if (isWidgetSessionErrorMessage(event.data, agentId)) {
        const pending = sessions.get(event.data.requestId);
        if (!pending) return;
        window.clearTimeout(pending.timeout);
        sessions.delete(event.data.requestId);
        pending.reject(new Error(event.data.message));
      }
    };
    window.addEventListener('message', receive);
    const ready: WidgetReadyMessage = { type: 'northstar:ready', agentId };
    window.parent.postMessage(ready, '*');
    return () => {
      window.removeEventListener('message', receive);
      for (const pending of sessions.values()) {
        window.clearTimeout(pending.timeout);
        pending.reject(new Error('Widget closed before a new session was created.'));
      }
      sessions.clear();
    };
  }, [agentId]);

  if (!initialization) return <PageLoader />;
  const agent = agentFromWidgetBootstrap(initialization.bootstrap);
  return <div className="standalone-widget"><ChatWidget agent={agent} embedded publicMode initialSession={initialization.session} requestNewSession={requestNewSession} /></div>;
}

function HostedWidgetPage() {
  const { agentId = 'northstar-guide' } = useParams();
  const agent = useApi(() => api.widget.hostedBootstrap(agentId), [agentId]);
  const requestNewSession = useCallback(() => api.widget.createHostedSession(agentId), [agentId]);

  if (agent.loading || !agent.data) return <PageLoader />;
  return <div className="demo-page">
    <header className="demo-header"><Brand /><div><Link to="/agents"><ArrowLeft /> Back to workspace</Link><Badge tone="success"><i className="status-dot" /> Live demo</Badge></div></header>
    <main>
      <section className="demo-copy">
        <span className="eyebrow-pill"><Sparkles /> AI answers grounded in your content</span>
        <h1>Meet <em>{agent.data.name}</em></h1>
        <p>This is the hosted version of your AI agent. Ask a question to test its tone, knowledge, and streamed response.</p>
        <div className="demo-benefits">
          <span><CheckCircle2 /><strong>Grounded answers</strong><small>Connected to trusted sources</small></span>
          <span><ShieldCheck /><strong>Privacy controls</strong><small>Sensitive data masking enabled</small></span>
          <span><Code2 /><strong>Deploy anywhere</strong><small>Widget, hosted page, or REST API</small></span>
        </div>
      </section>
      <section className="demo-widget-wrap"><ChatWidget agent={agent.data} embedded publicMode requestNewSession={requestNewSession} /></section>
    </main>
    <footer>&copy; 2026 Northstar AI &middot; <a href="#privacy">Privacy</a> &middot; <a href="#terms">Terms</a></footer>
  </div>;
}
