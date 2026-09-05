import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatWidget } from '@/components/chat-widget';
import { demoAgents } from '@/lib/demo-data';
import type { WidgetSession } from '@/types';

describe('ChatWidget public sessions', () => {
  beforeEach(() => {
    Object.defineProperty(HTMLElement.prototype, 'scrollTo', { configurable: true, value: vi.fn() });
  });
  afterEach(() => {
    cleanup();
    Reflect.deleteProperty(HTMLElement.prototype, 'scrollTo');
  });

  it('uses the supplied session factory for the first message and a reset', async () => {
    const first: WidgetSession = {
      conversationId: 'hosted-conversation-1', conversationPublicId: 'hosted-public-1', sessionToken: 'hosted-token-1', expiresAt: '2026-09-05T01:00:00Z',
    };
    const second: WidgetSession = {
      conversationId: 'hosted-conversation-2', conversationPublicId: 'hosted-public-2', sessionToken: 'hosted-token-2', expiresAt: '2026-09-05T02:00:00Z',
    };
    const requestNewSession = vi.fn()
      .mockResolvedValueOnce(first)
      .mockResolvedValueOnce(second);
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode('data: {"type":"token","content":"Hosted reply"}\n\ndata: {"type":"done","conversationId":"hosted-conversation-1"}\n\n'));
        controller.close();
      },
    });
    const fetchMock = vi.fn().mockResolvedValue(new Response(body, { status: 200, headers: { 'Content-Type': 'text/event-stream' } }));
    vi.stubGlobal('fetch', fetchMock);
    const user = userEvent.setup();

    render(<ChatWidget agent={demoAgents[0]!} embedded publicMode requestNewSession={requestNewSession} />);
    await user.type(screen.getByLabelText('Message'), 'Hello hosted widget');
    await user.click(screen.getByRole('button', { name: 'Send message' }));

    expect(await screen.findByText('Hosted reply')).toBeInTheDocument();
    expect(requestNewSession).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/widget/sessions/hosted-conversation-1/messages', expect.objectContaining({
      headers: expect.objectContaining({ Authorization: 'Bearer hosted-token-1' }),
    }));

    await user.click(screen.getByRole('button', { name: 'Start a new conversation' }));
    await waitFor(() => expect(requestNewSession).toHaveBeenCalledTimes(2));
  });
});
