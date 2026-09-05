import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AuthProvider, ToastProvider } from '@/components/providers';
import { demoAgents, demoIntegrations } from '@/lib/demo-data';
import { ApiError } from '@/lib/api';
import { MetaSignupError } from '@/lib/meta-whatsapp';
import { IntegrationsPage } from '@/pages/IntegrationsPage';
import type { WhatsAppBootstrap, WhatsAppConnection, WhatsAppStatus } from '@/types';

const apiMocks = vi.hoisted(() => ({
  listIntegrations: vi.fn(),
  setConnected: vi.fn(),
  listAgents: vi.fn(),
  bootstrap: vi.fn(),
  status: vi.fn(),
  complete: vi.fn(),
  disconnect: vi.fn(),
  loadFacebookSdk: vi.fn(),
  getFacebookLoginStatus: vi.fn(),
  logoutFacebook: vi.fn(),
  startSignup: vi.fn(),
  authSession: vi.fn(),
}));

vi.mock('@/lib/api', () => ({
  AUTH_SESSION_EVENT: 'northstar:session-changed',
  ApiError: class ApiError extends Error {
    constructor(message: string, readonly status: number, readonly detail?: unknown) { super(message); }
  },
  api: {
    auth: { session: apiMocks.authSession, login: vi.fn(), logout: vi.fn() },
    agents: { list: apiMocks.listAgents },
    integrations: {
      list: apiMocks.listIntegrations,
      setConnected: apiMocks.setConnected,
      whatsapp: {
        bootstrap: apiMocks.bootstrap,
        status: apiMocks.status,
        complete: apiMocks.complete,
        disconnect: apiMocks.disconnect,
      },
    },
  },
}));

vi.mock('@/lib/meta-whatsapp', () => ({
  MetaSignupError: class MetaSignupError extends Error {
    constructor(message: string, readonly reason: 'cancelled' | 'failed' | 'timeout' | 'expired' | 'aborted' | 'configuration') {
      super(message);
    }
  },
  loadFacebookSdk: apiMocks.loadFacebookSdk,
  getFacebookLoginStatus: apiMocks.getFacebookLoginStatus,
  logoutFacebook: apiMocks.logoutFacebook,
  startWhatsAppEmbeddedSignup: apiMocks.startSignup,
}));

const bootstrap: WhatsAppBootstrap = {
  appId: 'meta-app-id', configurationId: 'signup-configuration-id', signupSession: 'signed-signup-session', apiVersion: 'v26.0', enabled: true, connected: false, connection: null,
};
const disconnected: WhatsAppStatus = { enabled: true, connected: false, connection: null };
const connection: WhatsAppConnection = {
  wabaId: 'waba-1', phoneNumberId: 'phone-1', displayPhoneNumber: '+1 555 0100', verifiedName: 'Northstar Support',
  agentId: demoAgents[0]!.id, status: 'connected', connectedAt: '2026-09-04T12:00:00Z',
};

function renderPage() {
  return render(<ToastProvider><AuthProvider><IntegrationsPage /></AuthProvider></ToastProvider>);
}

describe('IntegrationsPage WhatsApp connection', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiMocks.authSession.mockReturnValue({
      accessToken: 'test-access', expiresAt: '2026-09-05T00:00:00Z',
      user: { id: 'owner-1', name: 'Owner', email: 'owner@example.com', role: 'owner' },
    });
    apiMocks.listIntegrations.mockResolvedValue(structuredClone(demoIntegrations));
    apiMocks.listAgents.mockResolvedValue(structuredClone(demoAgents));
    apiMocks.bootstrap.mockResolvedValue(bootstrap);
    apiMocks.status.mockResolvedValue(disconnected);
    apiMocks.loadFacebookSdk.mockResolvedValue({});
    apiMocks.getFacebookLoginStatus.mockResolvedValue('connected');
    apiMocks.logoutFacebook.mockResolvedValue('unknown');
    apiMocks.startSignup.mockResolvedValue({ code: 'one-time-code', wabaId: 'waba-1', phoneNumberId: 'phone-1' });
    apiMocks.complete.mockResolvedValue(connection);
    apiMocks.disconnect.mockResolvedValue(undefined);
  });

  afterEach(cleanup);

  it('requires an agent and six-digit PIN, then completes Meta signup server-side', async () => {
    const user = userEvent.setup();
    renderPage();
    const whatsappHeading = await screen.findByRole('heading', { name: 'WhatsApp' });
    const card = whatsappHeading.closest('section');
    expect(card).not.toBeNull();
    await user.click(within(card!).getByRole('button', { name: 'Connect' }));

    const continueButton = await screen.findByRole('button', { name: 'Continue with Facebook' });
    expect(continueButton).toBeDisabled();
    const agentSelect = screen.getByLabelText('Bot connection');
    expect(within(agentSelect).getAllByRole('option').map((option) => option.textContent)).toEqual(['Northstar Guide — Not connected', 'Sales Concierge — Not connected']);
    expect(within(agentSelect).queryByRole('option', { name: /Onboarding Coach/ })).not.toBeInTheDocument();
    await user.type(screen.getByLabelText('Two-step verification PIN'), '123456');
    expect(continueButton).toBeEnabled();
    await user.click(continueButton);

    expect(apiMocks.startSignup).toHaveBeenCalledWith({
      appId: bootstrap.appId, configurationId: bootstrap.configurationId, apiVersion: bootstrap.apiVersion,
    }, expect.any(AbortSignal));
    await waitFor(() => expect(apiMocks.complete).toHaveBeenCalledWith({
      code: 'one-time-code', wabaId: 'waba-1', phoneNumberId: 'phone-1', agentId: demoAgents[0]!.id, signupSession: 'signed-signup-session', twoStepVerificationPin: '123456',
    }));
    expect(await screen.findByText('+1 555 0100')).toBeInTheDocument();
    expect(within(card!).getByRole('button', { name: 'Manage' })).toBeInTheDocument();
  });

  it('shows the connected account and requires confirmation before disconnecting', async () => {
    apiMocks.listIntegrations.mockResolvedValue(demoIntegrations.map((item) => item.id === 'whatsapp' ? { ...item, connected: true } : item));
    apiMocks.bootstrap.mockResolvedValueOnce({ ...bootstrap, signupSession: 'original-signup-session', connected: true, connection }).mockResolvedValue({ ...bootstrap, signupSession: 'fresh-reconnect-session' });
    apiMocks.status.mockResolvedValueOnce({ enabled: true, connected: true, connection }).mockResolvedValue(disconnected);
    const user = userEvent.setup();
    renderPage();
    const whatsappHeading = await screen.findByRole('heading', { name: 'WhatsApp' });
    const card = whatsappHeading.closest('section');
    await user.click(within(card!).getByRole('button', { name: 'Manage' }));

    expect(await screen.findByText('Northstar Support')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Disconnect' }));
    expect(apiMocks.disconnect).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'Disconnect WhatsApp' }));
    await waitFor(() => expect(apiMocks.disconnect).toHaveBeenCalledWith(connection.agentId));
    expect(await screen.findByText('Connect in a few steps')).toBeInTheDocument();
    expect(apiMocks.bootstrap).toHaveBeenCalledTimes(2);

    await user.type(screen.getByLabelText('Two-step verification PIN'), '112233');
    await user.click(screen.getByRole('button', { name: 'Continue with Facebook' }));
    await waitFor(() => expect(apiMocks.complete).toHaveBeenCalledWith(expect.objectContaining({ signupSession: 'fresh-reconnect-session' })));
  });

  it('manages independent phone connections for multiple bots', async () => {
    const secondConnection = {
      ...connection,
      agentId: demoAgents[1]!.id,
      phoneNumberId: 'phone-2',
      displayPhoneNumber: '+1 555 0200',
      verifiedName: 'Northstar Sales',
    };
    apiMocks.listIntegrations.mockResolvedValue(demoIntegrations.map((item) => item.id === 'whatsapp' ? { ...item, connected: true } : item));
    apiMocks.bootstrap.mockResolvedValue({ ...bootstrap, connected: true, connection, connections: [connection, secondConnection] });
    apiMocks.status.mockResolvedValue({ enabled: true, connected: true, connection, connections: [connection, secondConnection] });
    const user = userEvent.setup();
    renderPage();
    const whatsappHeading = await screen.findByRole('heading', { name: 'WhatsApp' });
    await user.click(within(whatsappHeading.closest('section')!).getByRole('button', { name: 'Manage' }));

    expect(await screen.findByText('+1 555 0100')).toBeInTheDocument();
    expect(screen.getByText('2 connected')).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText('Bot connection'), demoAgents[1]!.id);
    expect(await screen.findByText('+1 555 0200')).toBeInTheDocument();
    expect(screen.getByText('Northstar Sales')).toBeInTheDocument();
    expect(screen.queryByText('+1 555 0100')).not.toBeInTheDocument();
  });

  it('shows the reusable Facebook session and lets the user log out without disconnecting WhatsApp', async () => {
    apiMocks.listIntegrations.mockResolvedValue(demoIntegrations.map((item) => item.id === 'whatsapp' ? { ...item, connected: true } : item));
    apiMocks.bootstrap.mockResolvedValue({ ...bootstrap, connected: true, connection });
    apiMocks.status.mockResolvedValue({ enabled: true, connected: true, connection });
    const user = userEvent.setup();
    renderPage();
    const whatsappHeading = await screen.findByRole('heading', { name: 'WhatsApp' });
    await user.click(within(whatsappHeading.closest('section')!).getByRole('button', { name: 'Manage' }));

    expect(await screen.findByText('Signed in')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Log out' }));
    await waitFor(() => expect(apiMocks.logoutFacebook).toHaveBeenCalledWith({
      appId: bootstrap.appId, configurationId: bootstrap.configurationId, apiVersion: bootstrap.apiVersion,
    }));
    expect(screen.getByText('Signed out')).toBeInTheDocument();
    expect(apiMocks.disconnect).not.toHaveBeenCalled();
    expect(screen.getByText('+1 555 0100')).toBeInTheDocument();
  });

  it('surfaces the safe backend error message when Meta cannot register a number', async () => {
    apiMocks.bootstrap.mockResolvedValueOnce(bootstrap).mockResolvedValue({ ...bootstrap, signupSession: 'replacement-signup-session' });
    apiMocks.complete.mockRejectedValueOnce(new ApiError('Request failed (502)', 502, {
      error: { code: 'http_502', message: 'Meta could not register the selected phone number' },
    }));
    const user = userEvent.setup();
    renderPage();
    const whatsappHeading = await screen.findByRole('heading', { name: 'WhatsApp' });
    await user.click(within(whatsappHeading.closest('section')!).getByRole('button', { name: 'Connect' }));
    await user.type(await screen.findByLabelText('Two-step verification PIN'), '654321');
    await user.click(screen.getByRole('button', { name: 'Continue with Facebook' }));

    expect(await screen.findByText('Meta could not register the selected phone number')).toBeInTheDocument();
    expect(apiMocks.bootstrap).toHaveBeenCalledTimes(2);
    expect(screen.getByLabelText('Two-step verification PIN')).toHaveValue('');
    await user.type(screen.getByLabelText('Two-step verification PIN'), '445566');
    await user.click(screen.getByRole('button', { name: 'Continue with Facebook' }));
    await waitFor(() => expect(apiMocks.complete).toHaveBeenLastCalledWith(expect.objectContaining({ signupSession: 'replacement-signup-session' })));
  });

  it('cannot close while the short-lived code is being finalized by the backend', async () => {
    let finishCompletion: ((value: WhatsAppConnection) => void) | undefined;
    apiMocks.complete.mockReturnValueOnce(new Promise<WhatsAppConnection>((resolve) => { finishCompletion = resolve; }));
    const user = userEvent.setup();
    renderPage();
    const whatsappHeading = await screen.findByRole('heading', { name: 'WhatsApp' });
    await user.click(within(whatsappHeading.closest('section')!).getByRole('button', { name: 'Connect' }));
    await user.type(await screen.findByLabelText('Two-step verification PIN'), '123456');
    await user.click(screen.getByRole('button', { name: 'Continue with Facebook' }));
    await waitFor(() => expect(apiMocks.complete).toHaveBeenCalled());

    const closeButton = screen.getByRole('button', { name: 'Close dialog' });
    expect(closeButton).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled();
    fireEvent.keyDown(window, { key: 'Escape' });
    fireEvent.mouseDown(document.querySelector('.modal-backdrop')!);
    expect(screen.getByRole('dialog')).toBeInTheDocument();

    finishCompletion?.(connection);
    expect(await screen.findByText('+1 555 0100')).toBeInTheDocument();
    expect(closeButton).toBeEnabled();
  });

  it('allows the user to abort while the Meta account picker is still open', async () => {
    let signupSignal: AbortSignal | undefined;
    apiMocks.startSignup.mockImplementationOnce((_configuration: unknown, signal: AbortSignal) => {
      signupSignal = signal;
      return new Promise(() => undefined);
    });
    const user = userEvent.setup();
    renderPage();
    const whatsappHeading = await screen.findByRole('heading', { name: 'WhatsApp' });
    await user.click(within(whatsappHeading.closest('section')!).getByRole('button', { name: 'Connect' }));
    await user.type(await screen.findByLabelText('Two-step verification PIN'), '123456');
    await user.click(screen.getByRole('button', { name: 'Continue with Facebook' }));

    const cancelSignup = await screen.findByRole('button', { name: 'Cancel signup' });
    expect(cancelSignup).toBeEnabled();
    await user.click(cancelSignup);
    expect(signupSignal?.aborted).toBe(true);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(apiMocks.complete).not.toHaveBeenCalled();

    await user.click(within(whatsappHeading.closest('section')!).getByRole('button', { name: 'Connect' }));
    expect(await screen.findByLabelText('Two-step verification PIN')).toHaveValue('');
  });

  it.each([
    ['cancelled', 'WhatsApp signup was cancelled.'],
    ['timeout', 'WhatsApp signup timed out.'],
    ['failed', 'Meta rejected WhatsApp signup.'],
  ] as const)('clears the PIN when Embedded Signup is %s', async (reason, message) => {
    apiMocks.startSignup.mockRejectedValueOnce(new MetaSignupError(message, reason));
    const user = userEvent.setup();
    renderPage();
    const whatsappHeading = await screen.findByRole('heading', { name: 'WhatsApp' });
    await user.click(within(whatsappHeading.closest('section')!).getByRole('button', { name: 'Connect' }));
    const pinInput = await screen.findByLabelText('Two-step verification PIN');
    await user.type(pinInput, '123456');
    await user.click(screen.getByRole('button', { name: 'Continue with Facebook' }));

    expect(await screen.findByText(message)).toBeInTheDocument();
    expect(pinInput).toHaveValue('');
    expect(apiMocks.complete).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Continue with Facebook' })).toBeDisabled();
  });

  it('does not present a stale authorization as a healthy connection', async () => {
    const expiredConnection = { ...connection, status: 'reconnect_required' };
    apiMocks.listIntegrations.mockResolvedValue(demoIntegrations.map((item) => item.id === 'whatsapp' ? { ...item, connected: true } : item));
    apiMocks.bootstrap.mockResolvedValue({ ...bootstrap, connected: true, connection: expiredConnection });
    apiMocks.status.mockResolvedValue({ enabled: true, connected: true, connection: expiredConnection });
    const user = userEvent.setup();
    renderPage();
    const whatsappHeading = await screen.findByRole('heading', { name: 'WhatsApp' });
    await user.click(within(whatsappHeading.closest('section')!).getByRole('button', { name: 'Manage' }));

    expect(await screen.findByText('Reconnect required')).toBeInTheDocument();
    expect(screen.getByText('This connection needs to be renewed')).toBeInTheDocument();
    expect(screen.queryByText('Webhook delivery is active')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Disconnect to reconnect' })).toBeInTheDocument();
  });

  it('shows the Meta setup path when Facebook Login is not configured', async () => {
    apiMocks.bootstrap.mockResolvedValue({
      ...bootstrap,
      appId: null,
      configurationId: null,
      signupSession: null,
      enabled: false,
    });
    apiMocks.status.mockResolvedValue({ enabled: false, connected: false, connection: null });
    const user = userEvent.setup();
    renderPage();
    const whatsappHeading = await screen.findByRole('heading', { name: 'WhatsApp' });
    await user.click(within(whatsappHeading.closest('section')!).getByRole('button', { name: 'Connect' }));

    expect(await screen.findByRole('heading', { name: 'Set up Facebook Login for WhatsApp' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Meta setup required' })).toBeDisabled();
    expect(screen.getByRole('link', { name: /Open Meta App Dashboard/ })).toHaveAttribute(
      'href',
      'https://developers.facebook.com/apps/',
    );
    expect(apiMocks.loadFacebookSdk).not.toHaveBeenCalled();
  });

  it('prevents non-admin members from starting an administrator-only Meta flow', async () => {
    apiMocks.authSession.mockReturnValue({
      accessToken: 'member-access', expiresAt: '2026-09-05T00:00:00Z',
      user: { id: 'member-1', name: 'Member', email: 'member@example.com', role: 'member' },
    });
    renderPage();
    const whatsappHeading = await screen.findByRole('heading', { name: 'WhatsApp' });
    const button = within(whatsappHeading.closest('section')!).getByRole('button', { name: 'Admin only' });

    expect(button).toBeDisabled();
    expect(screen.getByText('Admin access')).toBeInTheDocument();
    expect(apiMocks.bootstrap).not.toHaveBeenCalled();
  });
});
