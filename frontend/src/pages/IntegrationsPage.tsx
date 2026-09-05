import { BookOpen, Check, Code2, Database, Hash, MessageCircle, PlugZap, Search, Terminal, Users, Zap } from 'lucide-react';
import { useMemo, useState } from 'react';
import { useAuth, useToast } from '@/components/providers';
import { Badge, Button, Card, EmptyState, PageLoader } from '@/components/ui';
import { WhatsAppIntegrationModal } from '@/components/whatsapp-integration-modal';
import { api } from '@/lib/api';
import { useApi } from '@/lib/use-api';
import type { Integration } from '@/types';

const iconMap = { code: Code2, hash: Hash, message: MessageCircle, zap: Zap, book: BookOpen, terminal: Terminal, users: Users };

export function IntegrationsPage() {
  const result = useApi(() => api.integrations.list());
  const { session } = useAuth();
  const { pushToast } = useToast();
  const [search, setSearch] = useState('');
  const [category, setCategory] = useState<'all' | Integration['category']>('all');
  const [overrides, setOverrides] = useState<Record<string, boolean>>({});
  const [whatsAppOpen, setWhatsAppOpen] = useState(false);
  const canManageWhatsApp = session?.user.role === 'owner' || session?.user.role === 'admin';
  const items = useMemo(() => result.data?.filter((item) => (category === 'all' || item.category === category) && `${item.name} ${item.description}`.toLowerCase().includes(search.toLowerCase())) ?? [], [category, result.data, search]);

  const activate = async (item: Integration) => {
    if (item.id === 'whatsapp') {
      if (!canManageWhatsApp) return;
      setWhatsAppOpen(true);
      return;
    }
    const current = overrides[item.id] ?? item.connected;
    const next = !current;
    await api.integrations.setConnected(item.id, next);
    setOverrides((values) => ({ ...values, [item.id]: next }));
    pushToast(`${item.name} ${next ? 'connected' : 'disconnected'}`);
  };

  if (result.loading) return <PageLoader />;
  return <div className="page integrations-page">
    <div className="page-heading"><h2>Integrations</h2><p>Connect Northstar to your channels, content, and workflows.</p></div>
    <Card className="integration-hero"><div><span className="integration-hero__icon"><PlugZap /></span><span><Badge tone="purple">Integration marketplace</Badge><h3>Your AI agent, everywhere work happens.</h3><p>Keep knowledge in sync and give customers fast answers on every channel.</p></span></div><div className="integration-orbits"><span><Hash /></span><span><Database /></span><span><MessageCircle /></span><span><Zap /></span></div></Card>
    <div className="integration-toolbar"><div className="category-tabs">{(['all', 'channel', 'data', 'automation', 'developer'] as const).map((item) => <button key={item} className={category === item ? 'is-active' : ''} onClick={() => setCategory(item)}>{item === 'all' ? 'All integrations' : item}</button>)}</div><div className="search-box"><Search /><input aria-label="Search integrations" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search integrations" /></div></div>
    {items.length ? <div className="integration-grid">{items.map((item) => {
      const Icon = iconMap[item.icon as keyof typeof iconMap] ?? PlugZap;
      const connected = overrides[item.id] ?? item.connected;
      const requiresAdmin = item.id === 'whatsapp' && !canManageWhatsApp;
      return <Card className="integration-card" key={item.id}><div className="integration-card__icon"><Icon /></div><div className="integration-card__copy"><span><h3>{item.name}</h3>{item.comingSoon ? <Badge>Coming soon</Badge> : requiresAdmin ? <Badge>Admin access</Badge> : connected ? <Badge tone="success"><Check /> Connected</Badge> : null}</span><p>{item.description}</p></div><Button variant={connected ? 'secondary' : 'primary'} size="sm" disabled={item.comingSoon || requiresAdmin} title={requiresAdmin ? 'A workspace owner or admin must manage WhatsApp' : undefined} onClick={() => void activate(item)}>{item.comingSoon ? 'Notify me' : requiresAdmin ? 'Admin only' : connected ? 'Manage' : 'Connect'}</Button></Card>;
    })}</div> : <Card><EmptyState icon={PlugZap} title="No integrations found" description="Try another name or category." /></Card>}
    <WhatsAppIntegrationModal open={whatsAppOpen} onClose={() => setWhatsAppOpen(false)} onConnectionChanged={(connected) => setOverrides((values) => ({ ...values, whatsapp: connected }))} />
  </div>;
}
