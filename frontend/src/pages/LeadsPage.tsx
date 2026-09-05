import { ChevronDown, Download, Filter, Mail, Phone, Search, ShieldCheck, UserRound, UsersRound } from 'lucide-react';
import { useMemo, useState } from 'react';
import { useToast } from '@/components/providers';
import { Badge, Button, Card, EmptyState, PageLoader } from '@/components/ui';
import { api } from '@/lib/api';
import { relativeTime } from '@/lib/format';
import { useApi } from '@/lib/use-api';
import type { Lead } from '@/types';

const commonStatuses = ['new', 'contacted', 'qualified', 'converted', 'archived'];

function statusTone(status: string): 'brand' | 'warning' | 'success' | 'purple' | 'neutral' {
  if (status === 'new') return 'brand';
  if (status === 'contacted') return 'warning';
  if (status === 'qualified') return 'purple';
  if (status === 'converted') return 'success';
  return 'neutral';
}

function csvCell(value: unknown): string {
  const text = value == null ? '' : String(value);
  return `"${text.replace(/"/g, '""')}"`;
}

function downloadCsv(leads: Lead[]): void {
  const rows = [
    ['Name', 'Email', 'Phone', 'Status', 'Consent', 'Created'],
    ...leads.map((lead) => [lead.name ?? '', lead.email ?? '', lead.phone ?? '', lead.status, lead.consent ? 'yes' : 'no', lead.createdAt]),
  ];
  const blob = new Blob([rows.map((row) => row.map(csvCell).join(',')).join('\r\n')], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = `northstar-leads-${new Date().toISOString().slice(0, 10)}.csv`;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

export function LeadsPage() {
  const leads = useApi(() => api.leads.list());
  const agents = useApi(() => api.agents.list());
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState('all');
  const [overrides, setOverrides] = useState<Record<string, string>>({});
  const [updating, setUpdating] = useState<string>();
  const { pushToast } = useToast();

  const records = useMemo(() => (leads.data?.items ?? []).map((lead) => ({ ...lead, status: overrides[lead.id] ?? lead.status })), [leads.data, overrides]);
  const filtered = useMemo(() => {
    const query = search.trim().toLowerCase();
    return records.filter((lead) => {
      const matchesStatus = status === 'all' || lead.status === status;
      const matchesQuery = !query || [lead.name, lead.email, lead.phone, lead.status].some((value) => value?.toLowerCase().includes(query));
      return matchesStatus && matchesQuery;
    });
  }, [records, search, status]);
  const agentNames = useMemo(() => new Map((agents.data ?? []).map((agent) => [agent.id, agent.name])), [agents.data]);

  const updateStatus = async (lead: Lead, nextStatus: string) => {
    const previous = overrides[lead.id] ?? lead.status;
    setOverrides((current) => ({ ...current, [lead.id]: nextStatus }));
    setUpdating(lead.id);
    try {
      const saved = await api.leads.updateStatus(lead.id, nextStatus);
      setOverrides((current) => ({ ...current, [lead.id]: saved.status }));
      pushToast(`${lead.name || 'Lead'} marked ${saved.status}`);
    } catch (error) {
      setOverrides((current) => ({ ...current, [lead.id]: previous }));
      pushToast(error instanceof Error ? error.message : 'Could not update this lead', 'error');
    } finally {
      setUpdating(undefined);
    }
  };

  if (leads.loading || agents.loading) return <PageLoader />;
  if (leads.error) return <div className="page leads-page"><Card><EmptyState icon={UsersRound} title="Leads are unavailable" description={leads.error} action={<Button onClick={leads.reload}>Try again</Button>} /></Card></div>;

  const qualified = records.filter((lead) => lead.status === 'qualified').length;
  const converted = records.filter((lead) => lead.status === 'converted').length;
  const consented = records.filter((lead) => lead.consent).length;

  return <div className="page leads-page">
    <div className="page-heading page-heading--split">
      <div><h2>Leads</h2><p>Review contacts captured by your agents and move promising conversations forward.</p></div>
      <Button variant="secondary" icon={Download} disabled={!filtered.length} onClick={() => downloadCsv(filtered)}>Export CSV</Button>
    </div>

    <div className="leads-stats">
      <Card><span><UsersRound /></span><div><strong>{records.length}</strong><small>Total leads</small></div></Card>
      <Card><span><UserRound /></span><div><strong>{qualified}</strong><small>Qualified</small></div></Card>
      <Card><span><ShieldCheck /></span><div><strong>{consented}</strong><small>Consented</small></div></Card>
      <Card><span><Checkmark /></span><div><strong>{converted}</strong><small>Converted</small></div></Card>
    </div>

    <Card className="leads-card">
      <div className="leads-toolbar">
        <div><h3>Captured contacts</h3><p>{filtered.length} of {records.length} leads</p></div>
        <div className="leads-toolbar__filters">
          <div className="search-box"><Search /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search name, email, or phone" aria-label="Search leads" /></div>
          <div className="filter-select leads-status-filter"><Filter /><select value={status} onChange={(event) => setStatus(event.target.value)} aria-label="Filter leads by status"><option value="all">All statuses</option>{commonStatuses.map((item) => <option key={item} value={item}>{item}</option>)}</select><ChevronDown /></div>
        </div>
      </div>

      {filtered.length ? <div className="leads-table-wrap"><table className="leads-table">
        <thead><tr><th>Lead</th><th>Contact</th><th>Agent</th><th>Status</th><th>Consent</th><th>Captured</th></tr></thead>
        <tbody>{filtered.map((lead) => {
          const statuses = commonStatuses.includes(lead.status) ? commonStatuses : [lead.status, ...commonStatuses];
          return <tr key={lead.id}>
            <td><span className="lead-avatar">{(lead.name || lead.email || '?').slice(0, 1).toUpperCase()}</span><span><strong>{lead.name || 'Anonymous visitor'}</strong><small>{lead.conversationId ? 'From a conversation' : 'Direct capture'}</small></span></td>
            <td><span className="lead-contact">{lead.email ? <a href={`mailto:${lead.email}`}><Mail />{lead.email}</a> : null}{lead.phone ? <a href={`tel:${lead.phone}`}><Phone />{lead.phone}</a> : null}{!lead.email && !lead.phone ? <small>No contact details</small> : null}</span></td>
            <td>{agentNames.get(lead.agentId) ?? 'Unknown agent'}</td>
            <td><label className={`lead-status lead-status--${statusTone(lead.status)}`}><span className="sr-only">Status for {lead.name || 'lead'}</span><select value={lead.status} disabled={updating === lead.id} onChange={(event) => void updateStatus(lead, event.target.value)}>{statuses.map((item) => <option key={item} value={item}>{item}</option>)}</select><ChevronDown /></label></td>
            <td>{lead.consent ? <Badge tone="success"><ShieldCheck /> Granted</Badge> : <Badge>Not recorded</Badge>}</td>
            <td><time dateTime={lead.createdAt}>{relativeTime(lead.createdAt)}</time></td>
          </tr>;
        })}</tbody>
      </table></div> : <EmptyState icon={UsersRound} title="No leads found" description={search || status !== 'all' ? 'Try another search or status filter.' : 'New contacts captured by your agents will appear here.'} />}
    </Card>
  </div>;
}

function Checkmark() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12 4 4L19 6" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /></svg>;
}
