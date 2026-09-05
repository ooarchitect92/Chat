import { describe, expect, it, vi } from 'vitest';
import { api } from '@/lib/api';
import type { ChatStreamEvent, Session, WidgetBootstrap, WidgetSession } from '@/types';

describe('api client', () => {
  it('preserves a plain-text proxy error as an ApiError without reading the response twice', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('Upstream Meta gateway unavailable', {
      status: 502, headers: { 'Content-Type': 'text/plain' },
    })));

    await expect(api.integrations.whatsapp.status()).rejects.toMatchObject({
      name: 'ApiError',
      message: 'Request failed (502)',
      status: 502,
      detail: 'Upstream Meta gateway unavailable',
    });
  });

  it('persists a successful server session for later authenticated requests', async () => {
    const serverSession: Session = {
      accessToken: 'server-token',
      refreshToken: 'must-remain-http-only',
      expiresAt: '2026-09-05T12:00:00Z',
      user: { id: 'user-1', name: 'Test User', email: 'test@example.com', role: 'owner' },
    };
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(serverSession), { status: 200, headers: { 'Content-Type': 'application/json' } })));

    const browserSession: Session = {
      accessToken: serverSession.accessToken,
      expiresAt: serverSession.expiresAt,
      user: serverSession.user,
    };
    await expect(api.auth.login('test@example.com', 'secure-password')).resolves.toEqual(browserSession);
    expect(api.auth.session()).toEqual(browserSession);
  });

  it('parses structured server-sent chat events across a stream', async () => {
    const payload = [
      'data: {"type":"start","conversationId":"c1","messageId":"m1"}\n\n',
      'data: {"type":"token","content":"Hello"}\n\n',
      'data: {"type":"done","conversationId":"c1"}\n\n',
    ];
    const body = new ReadableStream<Uint8Array>({
      start(controller) { for (const frame of payload) controller.enqueue(new TextEncoder().encode(frame)); controller.close(); },
    });
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(body, { status: 200, headers: { 'Content-Type': 'text/event-stream' } })));
    const events: ChatStreamEvent[] = [];
    for await (const event of api.streamChat({ agentId: 'agent-1', message: 'Hi' })) events.push(event);

    expect(events).toEqual([
      { type: 'start', conversationId: 'c1', messageId: 'm1' },
      { type: 'token', content: 'Hello' },
      { type: 'done', conversationId: 'c1' },
    ]);
  });

  it('keeps public widget bootstrap independent from workspace authentication', async () => {
    const bootstrap: WidgetBootstrap = {
      agentId: 'agent-1',
      publicId: 'public-1',
      name: 'Support',
      avatar: 'S',
      appearance: {
        primaryColor: '#146cf6', surfaceColor: '#f6f8fb', position: 'bottom-right', launcherStyle: 'spark',
        welcomeTitle: 'Hello', welcomeMessage: 'How can I help?', placeholder: 'Ask a question', suggestedQuestions: [], showBranding: true,
      },
      collectEmail: false,
      sessionEndpoint: '/sessions',
      streamEndpoint: '/messages',
    };
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(bootstrap), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    vi.stubGlobal('fetch', fetchMock);
    localStorage.setItem('northstar.session', JSON.stringify({
      accessToken: 'private-workspace-token', expiresAt: '2026-09-05T12:00:00Z',
      user: { id: 'user-1', name: 'Test User', email: 'test@example.com', role: 'owner' },
    }));

    await api.widget.bootstrap('public-1');

    expect(fetchMock).toHaveBeenCalledWith('/api/v1/widget/public-1/bootstrap', expect.objectContaining({
      headers: { Accept: 'application/json' },
    }));
  });

  it('uses the dedicated unauthenticated routes for hosted widgets', async () => {
    const bootstrap: WidgetBootstrap = {
      agentId: 'agent-1', publicId: 'public-1', name: 'Hosted Support', avatar: 'H', collectEmail: false,
      sessionEndpoint: '/api/v1/widget/public-1/hosted/sessions', streamEndpoint: '/api/v1/widget/sessions/{conversationId}/messages',
      appearance: {
        primaryColor: '#146cf6', surfaceColor: '#f6f8fb', position: 'bottom-right', launcherStyle: 'spark',
        welcomeTitle: 'Hello', welcomeMessage: 'How can I help?', placeholder: 'Ask a question', suggestedQuestions: [], showBranding: true,
      },
    };
    const widgetSession: WidgetSession = {
      conversationId: 'conversation-1', conversationPublicId: 'public-conversation-1', sessionToken: 'hosted-token', expiresAt: '2026-09-05T12:00:00Z',
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(bootstrap), { status: 200, headers: { 'Content-Type': 'application/json' } }))
      .mockResolvedValueOnce(new Response(JSON.stringify(widgetSession), { status: 201, headers: { 'Content-Type': 'application/json' } }));
    vi.stubGlobal('fetch', fetchMock);
    localStorage.setItem('northstar.session', JSON.stringify({
      accessToken: 'private-workspace-token', expiresAt: '2026-09-05T12:00:00Z',
      user: { id: 'user-1', name: 'Test User', email: 'test@example.com', role: 'owner' },
    }));

    await expect(api.widget.hostedBootstrap('public-1')).resolves.toEqual(expect.objectContaining({ name: 'Hosted Support' }));
    await expect(api.widget.createHostedSession('public-1')).resolves.toEqual(widgetSession);

    expect(fetchMock).toHaveBeenNthCalledWith(1, '/api/v1/widget/public-1/hosted/bootstrap', expect.objectContaining({
      headers: { Accept: 'application/json' },
    }));
    expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/v1/widget/public-1/hosted/sessions', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ pageUrl: window.location.href }),
      headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
    }));
  });

  it('refreshes an expired session once and retries with rotated credentials', async () => {
    const expired: Session = {
      accessToken: 'expired-access',
      refreshToken: 'refresh-one',
      expiresAt: '2026-09-04T00:00:00Z',
      user: { id: 'user-1', name: 'Test User', email: 'test@example.com', role: 'owner' },
    };
    const rotated: Session = { ...expired, accessToken: 'fresh-access', refreshToken: 'refresh-two', expiresAt: '2026-09-05T00:00:00Z' };
    localStorage.setItem('northstar.session', JSON.stringify(expired));
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(null, { status: 401 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(rotated), { status: 200, headers: { 'Content-Type': 'application/json' } }))
      .mockResolvedValueOnce(new Response('[]', { status: 200, headers: { 'Content-Type': 'application/json' } }));
    vi.stubGlobal('fetch', fetchMock);

    await expect(api.agents.list()).resolves.toEqual([]);

    expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/v1/auth/refresh', expect.objectContaining({
      body: JSON.stringify({ refreshToken: 'refresh-one' }),
    }));
    expect(fetchMock.mock.calls[2]?.[1]).toEqual(expect.objectContaining({
      headers: expect.objectContaining({ Authorization: 'Bearer fresh-access' }),
    }));
    const browserSession: Session = {
      accessToken: rotated.accessToken,
      expiresAt: rotated.expiresAt,
      user: rotated.user,
    };
    expect(api.auth.session()).toEqual(browserSession);
  });

  it('rotates an HttpOnly cookie session without exposing a refresh token to JavaScript', async () => {
    const expired: Session = {
      accessToken: 'expired-cookie-access',
      expiresAt: '2026-09-04T00:00:00Z',
      user: { id: 'user-1', name: 'Test User', email: 'test@example.com', role: 'owner' },
    };
    const rotated: Session = {
      ...expired,
      accessToken: 'fresh-cookie-access',
      expiresAt: '2026-09-05T00:00:00Z',
    };
    localStorage.setItem('northstar.session', JSON.stringify(expired));
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(null, { status: 401 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(rotated), { status: 200, headers: { 'Content-Type': 'application/json' } }))
      .mockResolvedValueOnce(new Response('[]', { status: 200, headers: { 'Content-Type': 'application/json' } }));
    vi.stubGlobal('fetch', fetchMock);

    await expect(api.agents.list()).resolves.toEqual([]);

    expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/v1/auth/refresh', expect.objectContaining({
      body: undefined,
      credentials: 'include',
    }));
    expect(api.auth.session()).toEqual(rotated);
  });

  it('shares one refresh across concurrent 401 responses', async () => {
    const expired: Session = {
      accessToken: 'expired-access',
      refreshToken: 'shared-refresh',
      expiresAt: '2026-09-04T00:00:00Z',
      user: { id: 'user-1', name: 'Test User', email: 'test@example.com', role: 'analyst' },
    };
    const rotated: Session = { ...expired, accessToken: 'fresh-access', refreshToken: 'rotated-refresh' };
    localStorage.setItem('northstar.session', JSON.stringify(expired));
    let protectedCalls = 0;
    let refreshCalls = 0;
    let releaseRefresh: (() => void) | undefined;
    const refreshGate = new Promise<void>((resolve) => { releaseRefresh = resolve; });
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).endsWith('/auth/refresh')) {
        refreshCalls += 1;
        await refreshGate;
        return new Response(JSON.stringify(rotated), { status: 200, headers: { 'Content-Type': 'application/json' } });
      }
      protectedCalls += 1;
      return protectedCalls <= 2
        ? new Response(null, { status: 401 })
        : new Response('[]', { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    vi.stubGlobal('fetch', fetchMock);

    const agents = api.agents.list();
    const integrations = api.integrations.list();
    await vi.waitFor(() => expect(refreshCalls).toBe(1));
    releaseRefresh?.();

    await expect(Promise.all([agents, integrations])).resolves.toEqual([[], []]);
    expect(refreshCalls).toBe(1);
  });

  it('clears the session and notifies listeners when refresh fails', async () => {
    const expired: Session = {
      accessToken: 'expired-access',
      refreshToken: 'invalid-refresh',
      expiresAt: '2026-09-04T00:00:00Z',
      user: { id: 'user-1', name: 'Test User', email: 'test@example.com', role: 'member' },
    };
    localStorage.setItem('northstar.session', JSON.stringify(expired));
    const sessionChanged = vi.fn();
    window.addEventListener('northstar:session-changed', sessionChanged, { once: true });
    vi.stubGlobal('fetch', vi.fn()
      .mockResolvedValueOnce(new Response(null, { status: 401 }))
      .mockResolvedValueOnce(new Response(null, { status: 401 })));

    await expect(api.agents.list()).rejects.toMatchObject({ status: 401 });
    expect(api.auth.session()).toBeNull();
    expect(sessionChanged).toHaveBeenCalledOnce();
  });

  it('uploads files with constrained presigned POST fields before creating knowledge', async () => {
    vi.spyOn(crypto.subtle, 'digest').mockResolvedValue(new Uint8Array(32).buffer);
    const source = {
      id: 'source-1', agentId: 'agent-1', name: 'guide.pdf', kind: 'file' as const, status: 'processing' as const,
      sizeLabel: '1 KB', chunks: 0, updatedAt: '2026-09-04T00:00:00Z',
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({
        method: 'POST', url: 'https://uploads.example.test/', objectKey: 'tenant/guide.pdf',
        fields: { key: 'tenant/guide.pdf', policy: 'signed-policy' }, expiresAt: '2026-09-04T01:00:00Z',
      }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(source), { status: 201, headers: { 'Content-Type': 'application/json' } }));
    vi.stubGlobal('fetch', fetchMock);
    const file = new File(['pdf'], 'guide.pdf', { type: 'application/pdf' });

    await expect(api.knowledge.add('agent-1', { name: 'guide.pdf', kind: 'file', file })).resolves.toEqual(source);

    expect(JSON.parse(String((fetchMock.mock.calls[0]?.[1] as RequestInit).body))).toMatchObject({
      filename: 'guide.pdf',
      contentType: 'application/pdf',
      sizeBytes: 3,
      checksumSha256: '0'.repeat(64),
    });
    const uploadInit = fetchMock.mock.calls[1]?.[1] as RequestInit;
    expect(fetchMock.mock.calls[1]?.[0]).toBe('https://uploads.example.test/');
    expect(uploadInit.method).toBe('POST');
    expect(uploadInit.headers).toBeUndefined();
    expect(uploadInit.body).toBeInstanceOf(FormData);
    const form = uploadInit.body as FormData;
    expect(form.get('key')).toBe('tenant/guide.pdf');
    expect(form.get('policy')).toBe('signed-policy');
    expect(form.get('file')).toBe(file);
  });

  it('sends Embedded Signup credentials directly to the authenticated completion endpoint', async () => {
    const input = {
      code: 'short-lived-authorization-code',
      wabaId: '123456789',
      phoneNumberId: '987654321',
      agentId: '11111111-1111-1111-1111-111111111111',
      signupSession: 'server-signed-signup-session',
      twoStepVerificationPin: '246810',
    };
    const connection = {
      wabaId: input.wabaId, phoneNumberId: input.phoneNumberId, agentId: input.agentId,
      displayPhoneNumber: '+1 555 0100', verifiedName: 'Northstar Support', status: 'connected', connectedAt: '2026-09-04T00:00:00Z',
    };
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(connection), {
      status: 200, headers: { 'Content-Type': 'application/json' },
    }));
    vi.stubGlobal('fetch', fetchMock);

    await expect(api.integrations.whatsapp.complete(input)).resolves.toEqual(connection);
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/integrations/whatsapp/complete', expect.objectContaining({
      method: 'POST',
      credentials: 'include',
      body: JSON.stringify(input),
      headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
    }));
  });
});
