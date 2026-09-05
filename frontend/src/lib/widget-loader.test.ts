import { fireEvent, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import loaderSource from '../../public/widget.js?raw';
import type { WidgetBootstrap, WidgetSession } from '@/types';

describe('public widget loader', () => {
  const currentScriptDescriptor = Object.getOwnPropertyDescriptor(document, 'currentScript');

  afterEach(() => {
    document.body.innerHTML = '';
    if (currentScriptDescriptor) Object.defineProperty(document, 'currentScript', currentScriptDescriptor);
    else Reflect.deleteProperty(document, 'currentScript');
  });

  it('creates no session until the lazy iframe announces readiness', async () => {
    const bootstrap: WidgetBootstrap = {
      agentId: 'agent-1', publicId: 'public-1', name: 'Support', avatar: 'S', collectEmail: false,
      sessionEndpoint: '/api/v1/widget/public-1/sessions', streamEndpoint: '/api/v1/widget/sessions/{conversationId}/messages',
      appearance: { primaryColor: '#146cf6', surfaceColor: '#f6f8fb', position: 'bottom-right', launcherStyle: 'spark', welcomeTitle: 'Hello', welcomeMessage: 'Ask away', placeholder: 'Question', suggestedQuestions: [], showBranding: true },
    };
    const session: WidgetSession = { conversationId: 'conversation-1', conversationPublicId: 'public-conversation-1', sessionToken: 'scoped-bearer', expiresAt: '2026-09-05T00:00:00Z' };
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => String(input).endsWith('/bootstrap')
      ? new Response(JSON.stringify(bootstrap), { status: 200, headers: { 'Content-Type': 'application/json' } })
      : new Response(JSON.stringify(session), { status: 201, headers: { 'Content-Type': 'application/json' } }));
    vi.stubGlobal('fetch', fetchMock);
    const script = document.createElement('script');
    script.src = 'https://platform.example/widget.js';
    script.dataset.agentId = 'public-1';
    Object.defineProperty(document, 'currentScript', { configurable: true, value: script });

    Function(loaderSource)();

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const frame = await waitFor(() => {
      const value = document.querySelector('iframe');
      expect(value).not.toBeNull();
      return value as HTMLIFrameElement;
    });
    const launcher = document.querySelector('button') as HTMLButtonElement;
    expect(frame.hasAttribute('src')).toBe(false);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    fireEvent.click(launcher);
    expect(frame.src).toBe('https://platform.example/widget/public-1');
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const frameWindow = frame.contentWindow!;
    const postMessage = vi.spyOn(frameWindow, 'postMessage');
    window.dispatchEvent(new MessageEvent('message', { data: { type: 'northstar:ready', agentId: 'public-1' }, origin: 'https://platform.example', source: frameWindow }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(postMessage).toHaveBeenCalledWith({ type: 'northstar:init', agentId: 'public-1', bootstrap, session }, 'https://platform.example'));
  });
});
