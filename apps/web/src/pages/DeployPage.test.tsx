import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { ToastProvider } from '@/components/providers';
import { demoAgents } from '@/lib/demo-data';
import { DeployPage } from '@/pages/DeployPage';
import type { Agent, AgentPatch } from '@/types';

const apiMocks = vi.hoisted(() => ({
  list: vi.fn<() => Promise<Agent[]>>(),
  update: vi.fn<(agentId: string, patch: AgentPatch) => Promise<Agent>>(),
}));

vi.mock('@/lib/api', () => ({
  api: { agents: { list: apiMocks.list, update: apiMocks.update } },
}));

vi.mock('@/components/chat-widget', () => ({
  ChatWidget: ({ agent }: { agent: Agent }) => <div data-testid="widget-preview">{agent.appearance.welcomeTitle}</div>,
}));

function renderPage() {
  return render(<MemoryRouter><ToastProvider><DeployPage /></ToastProvider></MemoryRouter>);
}

describe('DeployPage', () => {
  let original: Agent;

  beforeEach(() => {
    original = structuredClone(demoAgents[0]!);
    apiMocks.list.mockResolvedValue([original]);
    apiMocks.update.mockImplementation(async (_agentId, patch) => ({
      ...original,
      ...patch,
      appearance: patch.appearance ?? original.appearance,
    }));
  });

  afterEach(cleanup);

  it('publishes appearance changes through the agent API and resets to the saved result', async () => {
    const user = userEvent.setup();
    renderPage();

    const heading = await screen.findByLabelText('Hero heading');
    const apply = screen.getByRole('button', { name: 'Apply' });
    expect(apply).toBeDisabled();

    await user.clear(heading);
    await user.type(heading, 'A sharper welcome');
    await user.click(screen.getByRole('button', { name: 'Bottom left' }));
    expect(screen.getByTestId('widget-preview')).toHaveTextContent('A sharper welcome');
    expect(apply).toBeEnabled();

    await user.click(apply);
    await waitFor(() => expect(apiMocks.update).toHaveBeenCalledTimes(1));
    expect(apiMocks.update).toHaveBeenCalledWith(original.id, {
      appearance: {
        ...original.appearance,
        position: 'bottom-left',
        welcomeTitle: 'A sharper welcome',
      },
      status: original.status,
    });
    await waitFor(() => expect(apply).toBeDisabled());

    await user.clear(heading);
    await user.type(heading, 'Discard this');
    await user.click(screen.getByRole('button', { name: 'Reset changes' }));
    expect(heading).toHaveValue('A sharper welcome');
    expect(apiMocks.update).toHaveBeenCalledTimes(1);
  });
});
