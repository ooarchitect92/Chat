import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ToastProvider } from '@/components/providers';
import { demoAgents, demoLeads } from '@/lib/demo-data';
import { LeadsPage } from '@/pages/LeadsPage';
import type { Agent, Lead, PageResult } from '@/types';

const apiMocks = vi.hoisted(() => ({
  listLeads: vi.fn<() => Promise<PageResult<Lead>>>(),
  listAgents: vi.fn<() => Promise<Agent[]>>(),
  updateStatus: vi.fn<(leadId: string, status: string) => Promise<Lead>>(),
}));

vi.mock('@/lib/api', () => ({
  api: {
    agents: { list: apiMocks.listAgents },
    leads: { list: apiMocks.listLeads, updateStatus: apiMocks.updateStatus },
  },
}));

describe('LeadsPage', () => {
  beforeEach(() => {
    const leads = structuredClone(demoLeads);
    apiMocks.listAgents.mockResolvedValue(structuredClone(demoAgents));
    apiMocks.listLeads.mockResolvedValue({ items: leads, total: leads.length, page: 1, pageSize: 50 });
    apiMocks.updateStatus.mockImplementation(async (leadId, status) => ({ ...leads.find((lead) => lead.id === leadId)!, status }));
  });

  afterEach(cleanup);

  it('filters captured contacts and persists a status change', async () => {
    const user = userEvent.setup();
    render(<ToastProvider><LeadsPage /></ToastProvider>);

    expect(await screen.findByText('Nora Reed')).toBeInTheDocument();
    await user.type(screen.getByLabelText('Search leads'), 'Arjun');
    expect(screen.getByText('Arjun Mehta')).toBeInTheDocument();
    expect(screen.queryByText('Nora Reed')).not.toBeInTheDocument();

    await user.clear(screen.getByLabelText('Search leads'));
    await user.selectOptions(screen.getByRole('combobox', { name: 'Status for Nora Reed' }), 'converted');
    await waitFor(() => expect(apiMocks.updateStatus).toHaveBeenCalledWith('lead-1', 'converted'));
  });
});
