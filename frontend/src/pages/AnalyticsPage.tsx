import { ArrowDownRight, ArrowUpRight, Bot, CheckCircle2, Clock3, Download, HelpCircle, MessageSquareText, Star } from 'lucide-react';
import { Badge, Button, Card, PageLoader } from '@/components/ui';
import { api } from '@/lib/api';
import { formatCompact, formatPercent } from '@/lib/format';
import { useApi } from '@/lib/use-api';

export function AnalyticsPage() {
  const result = useApi(() => api.analytics.summary()); const data = result.data;
  if (result.loading || !data) return <PageLoader />;
  const cards = [
    { label: 'Total conversations', value: formatCompact(data.conversations), delta: data.conversationsDelta, icon: MessageSquareText, color: 'blue' },
    { label: 'AI resolution rate', value: formatPercent(data.resolutionRate), delta: data.resolutionDelta, icon: CheckCircle2, color: 'green' },
    { label: 'Average response', value: `${data.avgResponseSeconds}s`, delta: -18, icon: Clock3, color: 'purple', inverse: true },
    { label: 'Visitor satisfaction', value: `${data.satisfaction}/5`, delta: 6.4, icon: Star, color: 'orange' },
  ];
  return <div className="page analytics-page"><div className="page-heading page-heading--split"><div><h2>Analytics</h2><p>Understand performance, find knowledge gaps, and improve outcomes.</p></div><div className="heading-actions"><select defaultValue="30" aria-label="Analytics period"><option value="7">Last 7 days</option><option value="30">Last 30 days</option><option value="90">Last 90 days</option></select><Button variant="secondary" icon={Download}>Export</Button></div></div>
    <div className="stat-grid">{cards.map(({ label, value, delta, icon: Icon, color, inverse }) => <Card className="stat-card analytics-stat" key={label}><div className={`stat-card__icon stat-card__icon--${color}`}><Icon /></div><span>{label}<HelpCircle /></span><strong>{value}</strong><small className={(inverse ? delta < 0 : delta > 0) ? 'positive' : 'negative'}>{delta > 0 ? <ArrowUpRight /> : <ArrowDownRight />}{Math.abs(delta)}% <em>vs previous period</em></small></Card>)}</div>
    <Card className="analytics-chart-card"><div className="card-heading"><div><h3>Conversations & resolutions</h3><p>Daily volume for all active agents</p></div><div className="chart-legend"><span><i className="legend-dot legend-dot--blue"/> Conversations</span><span><i className="legend-dot legend-dot--mint"/> Resolved by AI</span></div></div><AnalyticsChart data={data.chart} /></Card>
    <div className="analytics-lower-grid"><Card className="top-questions"><div className="card-heading"><div><h3>Top questions</h3><p>What visitors ask most often</p></div><Badge tone="brand">Knowledge insights</Badge></div><div className="question-list">{data.topQuestions.map((item, index) => <div key={item.question}><span className="question-rank">{index + 1}</span><span><strong>{item.question}</strong><small>{item.count} conversations</small></span><div className="resolution-bar"><span><i style={{ width: `${item.resolutionRate}%` }} /></span><small>{item.resolutionRate}% resolved</small></div></div>)}</div></Card>
      <Card className="channel-card"><div className="card-heading"><div><h3>Channels</h3><p>Where conversations begin</p></div></div><div className="donut-wrap"><div className="donut" style={{ background: `conic-gradient(#146cf6 0 68%, #705cf6 68% 87%, #25b898 87% 96%, #dfe4ec 96% 100%)` }}><span><strong>{formatCompact(data.conversations)}</strong><small>Total</small></span></div><div className="channel-legend">{data.channels.map((item, index) => <span key={item.channel}><i className={`channel-color channel-color--${index}`} /><strong>{item.channel}</strong><em>{item.value}%</em></span>)}</div></div></Card>
    </div>
    <Card className="health-insight"><span><Bot /></span><div><h3>Agent health is strong</h3><p>Knowledge coverage is up 7% this period. Billing questions are the largest remaining escalation driver.</p></div><Button variant="secondary">View knowledge gaps</Button></Card>
  </div>;
}

function AnalyticsChart({ data }: { data: Array<{ label: string; conversations: number; resolved: number }> }) {
  const max = Math.max(...data.map((item) => item.conversations), 1);
  return <div className="bar-chart"><div className="bar-y-labels"><span>{max}</span><span>{Math.round(max * .66)}</span><span>{Math.round(max * .33)}</span><span>0</span></div><div className="bar-plot"><div className="bar-grid"><i/><i/><i/><i/></div>{data.map((item) => <div className="bar-group" key={item.label}><div className="bar-pair"><i style={{ height: `${item.conversations / max * 100}%` }} title={`${item.conversations} conversations`} /><i style={{ height: `${item.resolved / max * 100}%` }} title={`${item.resolved} resolved`} /></div><span>{item.label.replace('Aug ', '').replace('Sep ', 'S')}</span></div>)}</div></div>;
}
