import {
  AlertCircle, Bot, Building2, CheckCircle2, ExternalLink, LoaderCircle, LockKeyhole,
  MessageCircle, Phone, RefreshCw, ShieldCheck, Unplug,
} from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useToast } from '@/components/providers';
import { Badge, Button, Field, Modal, StatusDot } from '@/components/ui';
import { ApiError, api } from '@/lib/api';
import { loadFacebookSdk, MetaSignupError, startWhatsAppEmbeddedSignup } from '@/lib/meta-whatsapp';
import type { Agent, WhatsAppBootstrap, WhatsAppConnection } from '@/types';

interface WhatsAppIntegrationModalProps {
  open: boolean;
  onClose: () => void;
  onConnectionChanged: (connected: boolean) => void;
}

type Operation = 'idle' | 'waiting-meta' | 'finalizing' | 'disconnecting';

function readableError(error: unknown): string {
  if (error instanceof MetaSignupError) return error.message;
  if (error instanceof ApiError) {
    if (error.detail && typeof error.detail === 'object') {
      const envelope = error.detail as { detail?: unknown; error?: { message?: unknown } };
      if (typeof envelope.error?.message === 'string') return envelope.error.message;
      if (typeof envelope.detail === 'string') return envelope.detail;
    }
    if (typeof error.detail === 'string') return error.detail;
  }
  return error instanceof Error ? error.message : 'Something went wrong. Please try again.';
}

function displayDate(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(parsed);
}

function metaConfiguration(value: WhatsAppBootstrap | null) {
  if (!value?.appId || !value.configurationId) return null;
  return { appId: value.appId, configurationId: value.configurationId, apiVersion: value.apiVersion };
}

export function WhatsAppIntegrationModal({ open, onClose, onConnectionChanged }: WhatsAppIntegrationModalProps) {
  const { pushToast } = useToast();
  const [configuration, setConfiguration] = useState<WhatsAppBootstrap | null>(null);
  const [connection, setConnection] = useState<WhatsAppConnection | null>(null);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [agentId, setAgentId] = useState('');
  const [pin, setPin] = useState('');
  const [loading, setLoading] = useState(false);
  const [sdkReady, setSdkReady] = useState(false);
  const [operation, setOperationState] = useState<Operation>('idle');
  const [confirmDisconnect, setConfirmDisconnect] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const signupAbort = useRef<AbortController | null>(null);
  const operationRef = useRef<Operation>('idle');
  const refreshError = useRef<string | null>(null);
  const setOperation = useCallback((next: Operation) => {
    operationRef.current = next;
    setOperationState(next);
  }, []);

  useEffect(() => {
    if (!open) return;
    let active = true;
    setLoading(true);
    setSdkReady(false);
    setConfirmDisconnect(false);
    setError(refreshError.current);
    refreshError.current = null;
    setPin('');
    setOperation('idle');

    void Promise.all([
      api.integrations.whatsapp.bootstrap(),
      api.integrations.whatsapp.status(),
      api.agents.list(),
    ]).then(async ([bootstrap, status, agentItems]) => {
      if (!active) return;
      const current = status.connection ?? bootstrap.connection ?? null;
      setConfiguration({ ...bootstrap, enabled: status.enabled, connected: status.connected, connection: current });
      setConnection(current);
      setAgents(agentItems);
      setAgentId(current?.agentId ?? agentItems.find((agent) => agent.status === 'active')?.id ?? '');
      const sdkConfiguration = metaConfiguration(bootstrap);
      if (bootstrap.enabled && sdkConfiguration && bootstrap.signupSession) {
        try {
          await loadFacebookSdk(sdkConfiguration);
          if (active) setSdkReady(true);
        } catch (sdkError) {
          if (active) setError(readableError(sdkError));
        }
      } else if (bootstrap.enabled && sdkConfiguration && active) {
        setError('A secure WhatsApp signup session could not be created. Please retry.');
      } else if (bootstrap.enabled && active) {
        setError('The Meta application configuration is incomplete. Ask a workspace administrator to review the server settings.');
      }
    }).catch((loadError: unknown) => {
      if (active) setError(readableError(loadError));
    }).finally(() => {
      if (active) setLoading(false);
    });

    return () => {
      active = false;
      signupAbort.current?.abort();
      signupAbort.current = null;
    };
  }, [open, revision, setOperation]);

  const selectedAgent = useMemo(() => agents.find((agent) => agent.id === (connection?.agentId ?? agentId)), [agentId, agents, connection?.agentId]);
  const activeAgents = useMemo(() => agents.filter((agent) => agent.status === 'active'), [agents]);
  const pinValid = /^\d{6}$/.test(pin);
  const busy = operation !== 'idle';
  const closeDisabled = operation === 'finalizing' || operation === 'disconnecting';
  const connectionHealthy = connection?.status.toLowerCase() === 'connected';

  const close = useCallback(() => {
    if (operationRef.current === 'finalizing' || operationRef.current === 'disconnecting') return;
    signupAbort.current?.abort();
    signupAbort.current = null;
    setPin('');
    setOperation('idle');
    onClose();
  }, [onClose, setOperation]);

  const connect = () => {
    const sdkConfiguration = metaConfiguration(configuration);
    const signupSession = configuration?.signupSession;
    if (!configuration?.enabled || !signupSession || !sdkConfiguration || !sdkReady || !activeAgents.some((agent) => agent.id === agentId) || !pinValid || busy) return;
    const controller = new AbortController();
    signupAbort.current?.abort();
    signupAbort.current = controller;
    setError(null);

    let signup: Promise<{ code: string; wabaId: string; phoneNumberId: string }>;
    try {
      // Meta requires FB.login to run synchronously from the user's click gesture.
      signup = startWhatsAppEmbeddedSignup(sdkConfiguration, controller.signal);
    } catch (signupError) {
      if (signupAbort.current === controller) signupAbort.current = null;
      setPin('');
      setError(readableError(signupError));
      return;
    }
    setOperation('waiting-meta');
    void signup.then((selection) => {
      // Meta's code is very short-lived. Lock the modal and exchange it
      // immediately; hiding this state would make success ambiguous.
      setOperation('finalizing');
      // The server consumes this signed session even if Meta registration
      // fails, so make it impossible for the browser to reuse it.
      setConfiguration((current) => current ? { ...current, signupSession: null } : current);
      return api.integrations.whatsapp.complete({ ...selection, agentId, signupSession, twoStepVerificationPin: pin });
    }).then((connected) => {
      setConnection(connected);
      setConfiguration((current) => current ? { ...current, connected: true, connection: connected } : current);
      setPin('');
      onConnectionChanged(true);
      pushToast(`${connected.displayPhoneNumber || 'WhatsApp number'} connected`);
    }).catch((signupError: unknown) => {
      // Never retain the registration credential after Meta rejects, cancels,
      // aborts, or times out the Embedded Signup attempt.
      setPin('');
      if (signupError instanceof MetaSignupError && signupError.reason === 'aborted') return;
      const message = readableError(signupError);
      if (operationRef.current === 'finalizing') {
        // Fetch a fresh one-use session automatically while preserving the
        // useful Meta/backend error for the operator.
        refreshError.current = message;
        setRevision((value) => value + 1);
      } else {
        setError(message);
      }
    }).finally(() => {
      if (signupAbort.current === controller) {
        signupAbort.current = null;
        setOperation('idle');
      }
    });
  };

  const disconnect = async () => {
    if (busy) return;
    setOperation('disconnecting');
    setError(null);
    try {
      await api.integrations.whatsapp.disconnect();
      setConnection(null);
      setConfiguration((current) => current ? { ...current, connected: false, connection: null } : current);
      setConfirmDisconnect(false);
      onConnectionChanged(false);
      pushToast('WhatsApp disconnected');
      // The previous signup session was consumed by the original connection.
      // Reload before offering the reconnect button.
      setRevision((value) => value + 1);
    } catch (disconnectError) {
      setError(readableError(disconnectError));
    } finally {
      setOperation('idle');
    }
  };

  const retry = () => {
    setPin('');
    setRevision((value) => value + 1);
  };
  const metaSetupRequired = Boolean(configuration && (!configuration.enabled || !metaConfiguration(configuration)));
  const canConnect = Boolean(configuration?.enabled && configuration.signupSession && metaConfiguration(configuration) && sdkReady && activeAgents.some((agent) => agent.id === agentId) && pinValid && !busy);

  return <Modal
    open={open}
    onClose={close}
    closeDisabled={closeDisabled}
    title={connection ? 'Manage WhatsApp' : 'Connect WhatsApp'}
    description={connection ? 'Review the Cloud API number connected to this workspace.' : 'Sign in with Facebook and choose or add a WhatsApp Cloud API number.'}
    size="lg"
    footer={confirmDisconnect
      ? <><Button variant="secondary" onClick={() => setConfirmDisconnect(false)} disabled={busy}>Keep connected</Button><Button variant="danger" icon={busy ? LoaderCircle : Unplug} onClick={() => void disconnect()} disabled={busy}>{busy ? 'Disconnecting…' : 'Disconnect WhatsApp'}</Button></>
      : connection
        ? <Button onClick={close} disabled={closeDisabled}>Done</Button>
        : <><Button variant="secondary" onClick={close} disabled={closeDisabled}>{operation === 'waiting-meta' ? 'Cancel signup' : 'Cancel'}</Button><Button className="meta-connect-button" onClick={connect} disabled={!canConnect}>{busy ? <LoaderCircle className="spin" /> : <span className="meta-f" aria-hidden="true">f</span>}{operation === 'waiting-meta' ? 'Complete in Meta…' : operation === 'finalizing' ? 'Connecting number…' : sdkReady ? 'Continue with Facebook' : metaSetupRequired ? 'Meta setup required' : 'Loading Meta Login…'}</Button></>}
  >
    <div className="whatsapp-manager" aria-busy={loading || busy}>
      <div className="whatsapp-manager__brand">
        <span className="whatsapp-logo" aria-hidden="true"><MessageCircle /></span>
        <div><strong>WhatsApp Business Platform</strong><small>Official Meta Cloud API connection</small></div>
        {connection ? <Badge tone={connectionHealthy ? 'success' : 'warning'}><StatusDot tone={connectionHealthy ? 'success' : 'warning'} /> {connectionHealthy ? 'Connected' : 'Reconnect required'}</Badge> : configuration?.enabled ? <Badge tone={sdkReady ? 'brand' : 'neutral'}>{sdkReady ? 'Ready' : 'Loading'}</Badge> : null}
      </div>

      {loading ? <div className="whatsapp-loading"><LoaderCircle className="spin" /><strong>Checking your Meta connection…</strong><small>This only takes a moment.</small></div> : null}

      {!loading && error ? <div className="integration-error" role="alert"><AlertCircle /><span><strong>We couldn’t complete that step</strong><small>{error}</small></span>{!busy && !connection ? <Button variant="secondary" size="sm" icon={RefreshCw} onClick={retry}>Retry</Button> : null}</div> : null}

      {!loading && metaSetupRequired ? <div className="integration-setup-required"><LockKeyhole /><div><h3>Set up Facebook Login for WhatsApp</h3><p>Create a Meta Business app with WhatsApp and Facebook Login for Business, then create an Embedded Signup configuration. Add the App ID, configuration ID, App Secret, webhook verification token, and encryption key to the server <code>.env</code> file and run <code>start.bat</code> again. Secrets must never be entered in this browser.</p><a className="button button--primary button--sm meta-setup-link" href="https://developers.facebook.com/apps/" target="_blank" rel="noreferrer">Open Meta App Dashboard <ExternalLink /></a></div></div> : null}

      {!loading && connection ? <>
        <div className="whatsapp-number-card">
          <span className="whatsapp-number-card__icon"><Phone /></span>
          <div className="whatsapp-number-card__title"><small>Connected number</small><h3>{connection.displayPhoneNumber || 'WhatsApp phone number'}</h3><p>{connection.verifiedName || 'WhatsApp Business'}</p></div>
          <Badge tone={connection.status.toLowerCase() === 'connected' ? 'success' : 'warning'}>{connection.status}</Badge>
        </div>
        <dl className="whatsapp-details">
          <div><dt><Building2 /> WhatsApp Business Account</dt><dd>{connection.wabaId}</dd></div>
          <div><dt><Phone /> Phone number ID</dt><dd>{connection.phoneNumberId}</dd></div>
          <div><dt><Bot /> Replies with</dt><dd>{selectedAgent?.name ?? 'Unknown agent'}</dd></div>
          <div><dt><CheckCircle2 /> Connected</dt><dd>{displayDate(connection.connectedAt)}</dd></div>
        </dl>
        {connectionHealthy
          ? <div className="whatsapp-info-note"><ShieldCheck /><span><strong>Webhook delivery is active</strong><small>Inbound messages will be verified by Meta signature and routed to {selectedAgent?.name ?? 'the selected agent'}.</small></span></div>
          : <div className="whatsapp-reconnect-note" role="alert"><AlertCircle /><span><strong>This connection needs to be renewed</strong><small>Northstar cannot reply until you disconnect this stale authorization and complete Meta signup again.</small></span></div>}
        {confirmDisconnect ? <div className="disconnect-confirm" role="alert"><AlertCircle /><div><strong>{connectionHealthy ? 'Disconnect this number?' : 'Disconnect before reconnecting?'}</strong><p>Northstar will stop receiving and replying to new WhatsApp messages. Your Meta business assets and number will not be deleted.</p></div></div> : <div className="whatsapp-manager__actions"><Button variant="secondary" icon={Unplug} onClick={() => setConfirmDisconnect(true)}>{connectionHealthy ? 'Disconnect' : 'Disconnect to reconnect'}</Button></div>}
      </> : null}

      {!loading && configuration?.enabled && !connection ? <>
        <div className="signup-explainer">
          <h3>Connect in a few steps</h3>
          <ol>
            <li><span>1</span><div><strong>Sign in to Facebook</strong><small>Use an account with access to your Meta Business Portfolio.</small></div></li>
            <li><span>2</span><div><strong>Choose your business and WABA</strong><small>Select a Cloud API number already in the account, or add and verify a new number.</small></div></li>
            <li><span>3</span><div><strong>Confirm permissions</strong><small>Meta returns a one-time code that Northstar exchanges securely on the server.</small></div></li>
          </ol>
        </div>
        <div className="whatsapp-form">
          <Field label="AI agent" htmlFor="whatsapp-agent" hint="Incoming messages to the selected number will use this agent and its knowledge.">
            <select id="whatsapp-agent" value={agentId} onChange={(event) => setAgentId(event.target.value)} disabled={busy || activeAgents.length === 0}>
              {activeAgents.length === 0 ? <option value="">No active agents available</option> : activeAgents.map((agent) => <option key={agent.id} value={agent.id}>{agent.name}</option>)}
            </select>
          </Field>
          <Field label="Two-step verification PIN" htmlFor="whatsapp-pin" messageId="whatsapp-pin-help" hint="Choose a new six-digit PIN for a new number, or enter the existing PIN for a registered Cloud API number." error={!pin || pinValid ? undefined : 'Enter exactly six digits.'}>
            <input id="whatsapp-pin" type="password" inputMode="numeric" autoComplete="off" maxLength={6} value={pin} onChange={(event) => setPin(event.target.value.replace(/\D/g, '').slice(0, 6))} placeholder="6-digit PIN" disabled={busy} required aria-invalid={Boolean(pin && !pinValid)} aria-describedby="whatsapp-pin-help" />
          </Field>
        </div>
        <div className="whatsapp-security-note"><ShieldCheck /><span><strong>Your Meta credentials stay private</strong><small>Northstar never receives your Facebook password. Long-lived access tokens are encrypted and stored only by the backend.</small></span><a href="https://www.facebook.com/business/help" target="_blank" rel="noreferrer">Meta help <ExternalLink /></a></div>
        <p className="whatsapp-coexistence-note">Numbers connected to the WhatsApp Business App require Meta’s separate Coexistence onboarding flow and are not enabled by this standard Cloud API connection.</p>
      </> : null}
    </div>
  </Modal>;
}
