import { Bot, Copy, Ellipsis, LayoutGrid, List, MessageSquare, MoreHorizontal, Plus, Search, Sparkles, Trash2 } from 'lucide-react';
import { useEffect, useRef, useState, type FormEvent } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useToast } from '@/components/providers';
import { Badge, Button, Card, EmptyState, Field, Modal, PageLoader } from '@/components/ui';
import { api } from '@/lib/api';
import { formatCompact, relativeTime } from '@/lib/format';
import { useApi } from '@/lib/use-api';
import type { Agent } from '@/types';

const templates = [
  { id: 'blank', icon: Sparkles, name: 'Start from scratch', description: 'Build a custom agent your way.' },
  { id: 'support', icon: MessageSquare, name: 'Customer support', description: 'Resolve product and account questions.' },
  { id: 'lead', icon: Bot, name: 'Lead qualification', description: 'Capture and qualify inbound leads.' },
];

export function AgentsPage() {
  const result = useApi(() => api.agents.list()); const { pushToast } = useToast(); const navigate = useNavigate();
  const [search, setSearch] = useState(''); const [view, setView] = useState<'grid' | 'list'>('grid'); const [createOpen, setCreateOpen] = useState(false); const [deleteAgent, setDeleteAgent] = useState<Agent | null>(null);
  const filtered = result.data?.filter((agent) => `${agent.name} ${agent.description}`.toLowerCase().includes(search.toLowerCase())) ?? [];
  const remove = async () => { if (!deleteAgent) return; await api.agents.remove(deleteAgent.id); pushToast(`${deleteAgent.name} deleted`); setDeleteAgent(null); result.reload(); };
  if (result.loading && !result.data) return <PageLoader />;
  return <div className="page agents-page">
    <div className="page-heading page-heading--split"><div><h2>AI agents</h2><p>Create focused experts, train them on trusted content, and deploy anywhere.</p></div><Button icon={Plus} onClick={() => setCreateOpen(true)}>Create agent</Button></div>
    <div className="list-toolbar"><div className="search-box"><Search /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search agents…" aria-label="Search agents" /></div><div className="view-toggle" role="group" aria-label="View style"><button className={view === 'grid' ? 'is-active' : ''} onClick={() => setView('grid')} aria-label="Grid view"><LayoutGrid /></button><button className={view === 'list' ? 'is-active' : ''} onClick={() => setView('list')} aria-label="List view"><List /></button></div></div>
    {filtered.length ? <div className={`agent-cards agent-cards--${view}`}>{filtered.map((agent) => <AgentCard key={agent.id} agent={agent} onDelete={() => setDeleteAgent(agent)} />)}</div> : <Card><EmptyState icon={Bot} title={search ? 'No matching agents' : 'Create your first agent'} description={search ? 'Try a different search term.' : 'Start with a focused job and teach your agent from trusted sources.'} action={!search ? <Button icon={Plus} onClick={() => setCreateOpen(true)}>Create agent</Button> : undefined} /></Card>}
    <CreateAgentModal open={createOpen} onClose={() => setCreateOpen(false)} onCreated={(agent) => { setCreateOpen(false); result.reload(); pushToast('Agent created'); navigate(`/agents/${agent.id}/instructions`); }} />
    <Modal open={Boolean(deleteAgent)} onClose={() => setDeleteAgent(null)} title="Delete this agent?" description="This also removes its deployment settings. Conversation history is retained according to your data policy." size="sm" footer={<><Button variant="secondary" onClick={() => setDeleteAgent(null)}>Cancel</Button><Button variant="danger" icon={Trash2} onClick={() => void remove()}>Delete agent</Button></>}><div className="confirm-agent"><span className="agent-avatar" style={{ background: deleteAgent?.appearance.primaryColor }}>{deleteAgent?.avatar}</span><div><strong>{deleteAgent?.name}</strong><small>{deleteAgent?.knowledgeCount} knowledge sources · {deleteAgent?.conversations} conversations</small></div></div></Modal>
  </div>;
}

function AgentCard({ agent, onDelete }: { agent: Agent; onDelete: () => void }) {
  const [open, setOpen] = useState(false); const ref = useRef<HTMLDivElement>(null); const { pushToast } = useToast();
  useEffect(() => { const close = (event: MouseEvent) => { if (!ref.current?.contains(event.target as Node)) setOpen(false); }; document.addEventListener('mousedown', close); return () => document.removeEventListener('mousedown', close); }, []);
  const duplicate = async () => { await api.agents.create({ name: `${agent.name} copy`, description: agent.description }); pushToast('Agent duplicated'); setOpen(false); window.location.reload(); };
  return <Card className="agent-card">
    <div className="agent-card__top"><span className="agent-avatar agent-avatar--lg" style={{ background: agent.appearance.primaryColor }}>{agent.avatar}</span><Badge tone={agent.status === 'active' ? 'success' : agent.status === 'training' ? 'purple' : 'warning'}><i className="status-dot" /> {agent.status}</Badge><div className="more-menu" ref={ref}><button className="icon-button" onClick={() => setOpen((value) => !value)} aria-label={`Actions for ${agent.name}`}><MoreHorizontal /></button>{open ? <div className="menu-popover"><button onClick={() => void duplicate()}><Copy />Duplicate</button><button className="danger" onClick={onDelete}><Trash2 />Delete</button></div> : null}</div></div>
    <Link to={`/agents/${agent.id}/instructions`} className="agent-card__link"><h3>{agent.name}</h3><p>{agent.description}</p></Link>
    <div className="agent-card__stats"><span><strong>{formatCompact(agent.conversations)}</strong><small>Conversations</small></span><span><strong>{agent.resolutionRate}%</strong><small>Resolved</small></span><span><strong>{agent.knowledgeCount}</strong><small>Sources</small></span></div>
    <div className="agent-card__footer"><span>Updated {relativeTime(agent.lastUpdated)}</span><Link to={`/agents/${agent.id}/instructions`}>Open <Ellipsis /></Link></div>
  </Card>;
}

function CreateAgentModal({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: (agent: Agent) => void }) {
  const [name, setName] = useState(''); const [description, setDescription] = useState(''); const [template, setTemplate] = useState('blank'); const [saving, setSaving] = useState(false);
  useEffect(() => { if (!open) { setName(''); setDescription(''); setTemplate('blank'); } }, [open]);
  const submit = async (event: FormEvent) => { event.preventDefault(); if (!name.trim()) return; setSaving(true); try { onCreated(await api.agents.create({ name: name.trim(), description: description.trim() || 'A helpful AI agent', template })); } finally { setSaving(false); } };
  return <Modal open={open} onClose={onClose} title="Create an AI agent" description="Give it one clear job. You can adjust everything later." footer={<><Button variant="secondary" onClick={onClose}>Cancel</Button><Button disabled={!name.trim() || saving} onClick={() => document.getElementById('create-agent-form')?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))}>{saving ? 'Creating…' : 'Create agent'}</Button></>}>
    <form id="create-agent-form" onSubmit={(event) => void submit(event)}><Field label="Agent name" htmlFor="agent-name"><input id="agent-name" value={name} onChange={(event) => setName(event.target.value)} placeholder="e.g. Product Guide" maxLength={64} autoFocus /></Field><Field label="Short description" htmlFor="agent-description" hint="Shown to workspace members."><input id="agent-description" value={description} onChange={(event) => setDescription(event.target.value)} placeholder="What does this agent help with?" maxLength={120} /></Field><fieldset className="template-field"><legend>Choose a starting point</legend><div className="template-grid">{templates.map(({ id, icon: Icon, name: templateName, description: text }) => <label key={id} className={template === id ? 'is-selected' : ''}><input type="radio" name="template" value={id} checked={template === id} onChange={() => setTemplate(id)} /><Icon /><strong>{templateName}</strong><small>{text}</small></label>)}</div></fieldset></form>
  </Modal>;
}
