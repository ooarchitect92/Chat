import { ArrowRight, Bot, CheckCircle2, Clock3, Inbox, MessageSquareText, Plus, Sparkles, TrendingUp } from 'lucide-react';
import { Link } from 'react-router-dom';
import { useAuth } from '@/components/providers';
import { Badge, Button, Card, PageLoader } from '@/components/ui';
import { api } from '@/lib/api';
import { formatCompact, formatPercent, relativeTime } from '@/lib/format';
import { useApi } from '@/lib/use-api';

export function OverviewPage() {
  const { session } = useAuth();
  const agents = useApi(() => api.agents.list());
  const conversations = useApi(() => api.conversations.list());
  const analytics = useApi(() => api.analytics.summary());
  if (agents.loading && !agents.data) return <PageLoader />;
  const activeAgents = agents.data?.filter((agent) => agent.status === 'active').length ?? 0;
  const now = new Date();
  const greeting = now.getHours() < 12 ? 'Good morning' : now.getHours() < 18 ? 'Good afternoon' : 'Good evening';
  const displayName = session?.user.name.split(/\s+/)[0] || 'there';
  const currentDelta = analytics.data?.conversationsDelta ?? 0;
  const resolutionDelta = analytics.data?.resolutionDelta ?? 0;
  const stats = [
    { label: 'Conversations', value: analytics.data ? formatCompact(analytics.data.conversations) : '—', note: `${currentDelta > 0 ? '+' : ''}${currentDelta}% vs previous period`, icon: MessageSquareText, tone: 'blue' },
    { label: 'Resolved by AI', value: analytics.data ? formatPercent(analytics.data.resolutionRate) : '—', note: `${resolutionDelta > 0 ? '+' : ''}${resolutionDelta}% vs previous period`, icon: CheckCircle2, tone: 'green' },
    { label: 'Avg. response', value: analytics.data ? `${analytics.data.avgResponseSeconds}s` : '—', note: 'Across completed AI replies', icon: Clock3, tone: 'purple' },
    { label: 'Active agents', value: String(activeAgents), note: `${agents.data?.length ?? 0} agents total`, icon: Bot, tone: 'orange' },
  ];
  return <div className="page page--overview">
    <div className="page-heading page-heading--split"><div><span className="page-kicker">{new Intl.DateTimeFormat('en', { weekday: 'long', month: 'long', day: 'numeric' }).format(now)}</span><h2>{greeting}, {displayName} <span aria-hidden="true">👋</span></h2><p>Here’s what your AI team has been doing.</p></div><Link to="/agents"><Button icon={Plus}>Create agent</Button></Link></div>
    <div className="stat-grid">{stats.map(({ label, value, note, icon: Icon, tone }) => <Card key={label} className="stat-card"><div className={`stat-card__icon stat-card__icon--${tone}`}><Icon /></div><span>{label}</span><strong>{value}</strong><small><TrendingUp /> {note}</small></Card>)}</div>
    <div className="overview-grid">
      <Card className="activity-card">
        <div className="card-heading"><div><h3>Conversation volume</h3><p>Questions handled across all agents</p></div><select aria-label="Chart period" defaultValue="7"><option value="7">Last 7 days</option><option value="30">Last 30 days</option></select></div>
        <OverviewChart values={analytics.data?.chart.slice(-7).map((item) => item.conversations) ?? [42, 52, 48, 70, 61, 82, 96]} />
        <div className="chart-legend"><span><i className="legend-dot legend-dot--blue" /> Conversations</span><strong>{analytics.data ? formatCompact(analytics.data.conversations) : '—'} total</strong></div>
      </Card>
      <Card className="inbox-card">
        <div className="card-heading"><div><h3>Needs attention</h3><p>Open and escalated conversations</p></div><Link to="/conversations">View inbox <ArrowRight /></Link></div>
        <div className="attention-list">{conversations.data?.items.filter((item) => item.state !== 'resolved').slice(0, 3).map((conversation) => <Link to={`/conversations?selected=${conversation.id}`} key={conversation.id} className="attention-item"><span className="mini-avatar">{conversation.visitorName.charAt(0)}</span><span><strong>{conversation.visitorName}</strong><small>{conversation.preview}</small></span><span className="attention-meta">{conversation.state === 'escalated' ? <Badge tone="danger">Escalated</Badge> : <Badge tone="brand">Open</Badge>}<small>{relativeTime(conversation.updatedAt)}</small></span></Link>)}</div>
        {!conversations.data?.items.some((item) => item.state !== 'resolved') ? <div className="mini-empty"><Inbox /><p>You’re all caught up.</p></div> : null}
      </Card>
    </div>
    <Card className="agents-snapshot"><div className="card-heading"><div><h3>Your agents</h3><p>Performance at a glance</p></div><Link to="/agents">Manage agents <ArrowRight /></Link></div><div className="agent-row-list">{agents.data?.slice(0, 4).map((agent) => <Link className="agent-row" key={agent.id} to={`/agents/${agent.id}/instructions`}><span className="agent-avatar" style={{ background: agent.appearance.primaryColor }}>{agent.avatar}</span><span className="agent-row__main"><strong>{agent.name}</strong><small>{agent.description}</small></span><span className="agent-row__metric"><small>Conversations</small><strong>{formatCompact(agent.conversations)}</strong></span><span className="agent-row__metric"><small>Resolution</small><strong>{formatPercent(agent.resolutionRate)}</strong></span><span className="agent-row__status"><i className={`status-dot status-dot--${agent.status === 'active' ? 'success' : 'warning'}`} /> {agent.status}</span><ArrowRight className="row-arrow" /></Link>)}</div></Card>
    <aside className="tip-banner"><span><Sparkles /></span><div><strong>Make every answer sharper</strong><p>Review unresolved questions and add the missing information to your knowledge base.</p></div><Link to="/conversations">Review gaps <ArrowRight /></Link></aside>
  </div>;
}

function OverviewChart({ values }: { values: number[] }) {
  const width = 720; const height = 210; const pad = 16; const max = Math.max(...values, 1); const min = Math.min(...values, 0);
  const points = values.map((value, index) => `${pad + index * ((width - pad * 2) / Math.max(values.length - 1, 1))},${height - pad - ((value - min) / Math.max(max - min, 1)) * (height - pad * 2)}`).join(' ');
  const area = `${pad},${height - pad} ${points} ${width - pad},${height - pad}`;
  return <div className="overview-chart"><div className="chart-lines"><i/><i/><i/><i/></div><svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" aria-label="Conversation volume trend"><defs><linearGradient id="chartFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#146cf6" stopOpacity=".22"/><stop offset="1" stopColor="#146cf6" stopOpacity=".01"/></linearGradient></defs><polygon points={area} fill="url(#chartFill)"/><polyline points={points} fill="none" stroke="#146cf6" strokeWidth="4" strokeLinecap="round" strokeLinejoin="round"/>{values.map((value, index) => { const [x = 0, y = 0] = points.split(' ')[index]?.split(',').map(Number) ?? []; return <circle key={index} cx={x} cy={y} r="4.5" fill="white" stroke="#146cf6" strokeWidth="3"><title>{value} conversations</title></circle>; })}</svg><div className="chart-labels">{['Thu','Fri','Sat','Sun','Mon','Tue','Wed'].map((day) => <span key={day}>{day}</span>)}</div></div>;
}
