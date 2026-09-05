import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { WidgetDemoPage } from '@/pages/WidgetDemoPage';
import type { Agent, WidgetInitMessage, WidgetSession } from '@/types';

const widgetMocks = vi.hoisted(() => ({
  requestNewSession: undefined as (() => Promise<WidgetSession>) | undefined,
}));

vi.mock('@/components/chat-widget', () => ({
  ChatWidget: ({ agent, initialSession, requestNewSession }: { agent: Agent; initialSession?: WidgetSession; requestNewSession?: () => Promise<WidgetSession> }) => {
    widgetMocks.requestNewSession = requestNewSession;
    return <div data-testid="initialized-widget">{agent.name}:{initialSession?.conversationId}</div>;
  },
}));

describe('embedded widget handshake', () => {
  afterEach(() => {
    cleanup();
    widgetMocks.requestNewSession = undefined;
  });

  it('announces readiness and accepts initialization only from its parent window', async () => {
    const postMessage = vi.spyOn(window.parent, 'postMessage');
    render(<MemoryRouter initialEntries={['/widget/public-1']}><Routes><Route path="/widget/:agentId" element={<WidgetDemoPage embedded />} /></Routes></MemoryRouter>);

    await waitFor(() => expect(postMessage).toHaveBeenCalledWith({ type: 'northstar:ready', agentId: 'public-1' }, '*'));
    const initialization: WidgetInitMessage = {
      type: 'northstar:init',
      agentId: 'public-1',
      bootstrap: {
        agentId: 'agent-1', publicId: 'public-1', name: 'Secure Support', avatar: 'S', collectEmail: false,
        sessionEndpoint: '/api/v1/widget/public-1/sessions', streamEndpoint: '/api/v1/widget/sessions/{conversationId}/messages',
        appearance: { primaryColor: '#146cf6', surfaceColor: '#f6f8fb', position: 'bottom-right', launcherStyle: 'spark', welcomeTitle: 'Hello', welcomeMessage: 'Ask away', placeholder: 'Question', suggestedQuestions: [], showBranding: true },
      },
      session: { conversationId: 'conversation-1', conversationPublicId: 'public-conversation-1', sessionToken: 'scoped-bearer', expiresAt: '2026-09-05T00:00:00Z' },
    };

    act(() => window.dispatchEvent(new MessageEvent('message', { data: initialization, source: null })));
    expect(screen.queryByTestId('initialized-widget')).not.toBeInTheDocument();

    act(() => window.dispatchEvent(new MessageEvent('message', { data: initialization, source: window.parent })));
    expect(await screen.findByTestId('initialized-widget')).toHaveTextContent('Secure Support:conversation-1');

    let freshSession: Promise<WidgetSession> | undefined;
    act(() => { freshSession = widgetMocks.requestNewSession?.(); });
    const sessionRequest = postMessage.mock.calls.map(([message]) => message).find((message) => typeof message === 'object' && message !== null && 'type' in message && message.type === 'northstar:session-request');
    expect(sessionRequest).toEqual(expect.objectContaining({ agentId: 'public-1', requestId: expect.any(String) }));
    const requestId = (sessionRequest as { requestId: string }).requestId;
    const replacement: WidgetSession = { conversationId: 'conversation-2', conversationPublicId: 'public-conversation-2', sessionToken: 'fresh-scoped-bearer', expiresAt: '2026-09-05T01:00:00Z' };
    act(() => window.dispatchEvent(new MessageEvent('message', { data: { type: 'northstar:session', agentId: 'public-1', requestId, session: replacement }, source: window.parent })));
    await expect(freshSession).resolves.toEqual(replacement);
  });

  it('uses hosted bootstrap and session routes for a standalone demo', async () => {
    const bootstrap = {
      agentId: 'agent-1', publicId: 'public-1', name: 'Hosted Support', avatar: 'H', collectEmail: false,
      sessionEndpoint: '/api/v1/widget/public-1/hosted/sessions', streamEndpoint: '/api/v1/widget/sessions/{conversationId}/messages',
      appearance: {
        primaryColor: '#146cf6', surfaceColor: '#f6f8fb', position: 'bottom-right' as const, launcherStyle: 'spark' as const,
        welcomeTitle: 'Hello', welcomeMessage: 'Ask away', placeholder: 'Question', suggestedQuestions: [], showBranding: true,
      },
    };
    const session: WidgetSession = {
      conversationId: 'hosted-conversation', conversationPublicId: 'hosted-public-conversation', sessionToken: 'hosted-bearer', expiresAt: '2026-09-05T01:00:00Z',
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(bootstrap), { status: 200, headers: { 'Content-Type': 'application/json' } }))
      .mockResolvedValueOnce(new Response(JSON.stringify(session), { status: 201, headers: { 'Content-Type': 'application/json' } }));
    vi.stubGlobal('fetch', fetchMock);

    render(<MemoryRouter initialEntries={['/demo/public-1']}><Routes><Route path="/demo/:agentId" element={<WidgetDemoPage />} /></Routes></MemoryRouter>);

    expect(await screen.findByTestId('initialized-widget')).toHaveTextContent('Hosted Support:');
    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/v1/widget/public-1/hosted/bootstrap');
    expect(widgetMocks.requestNewSession).toBeTypeOf('function');
    await expect(widgetMocks.requestNewSession!()).resolves.toEqual(session);
    expect(fetchMock.mock.calls[1]?.[0]).toBe('/api/v1/widget/public-1/hosted/sessions');
  });
});
