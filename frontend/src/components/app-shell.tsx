import {
  BarChart3, Bell, ChevronDown, CircleHelp, Code2, Command, ContactRound, Inbox, LayoutDashboard, LogOut,
  PanelLeftClose, PanelLeftOpen, PlugZap, Search, Settings2, Sparkles,
} from 'lucide-react';
import { useMemo, useState } from 'react';
import { Link, NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { Brand } from '@/components/brand';
import { useAuth } from '@/components/providers';

const navigation = [
  { to: '/', label: 'Overview', icon: LayoutDashboard, end: true },
  { to: '/agents', label: 'Agents', icon: Sparkles },
  { to: '/conversations', label: 'Conversations', icon: Inbox },
  { to: '/leads', label: 'Leads', icon: ContactRound },
  { to: '/analytics', label: 'Analytics', icon: BarChart3 },
  { to: '/integrations', label: 'Integrations', icon: PlugZap },
  { to: '/deploy', label: 'Deploy', icon: Code2 },
];

function pageTitle(pathname: string): string {
  if (pathname === '/') return 'Overview';
  if (pathname.startsWith('/agents/') && pathname !== '/agents/new') return 'Agent workspace';
  const item = navigation.find((nav) => nav.to !== '/' && pathname.startsWith(nav.to));
  return item?.label ?? 'Workspace';
}

export function AppShell() {
  const [expanded, setExpanded] = useState(false);
  const [accountOpen, setAccountOpen] = useState(false);
  const { session, logout } = useAuth();
  const location = useLocation(); const navigate = useNavigate();
  const initials = useMemo(() => (session?.user.name || 'NS').split(' ').map((item) => item[0]).join('').slice(0, 2).toUpperCase(), [session]);
  return <div className={`app-shell ${expanded ? 'app-shell--expanded' : ''}`}>
    <aside className="rail" aria-label="Primary navigation">
      <Link to="/" className="rail__brand" aria-label="Northstar AI home"><Brand compact /></Link>
      <nav className="rail__nav">
        {navigation.map(({ to, label, icon: Icon, end }) => <NavLink key={to} to={to} end={end} className={({ isActive }) => `rail-link ${isActive ? 'is-active' : ''}`} title={label}><Icon /><span>{label}</span></NavLink>)}
      </nav>
      <div className="rail__bottom">
        <NavLink to="/settings" className={({ isActive }) => `rail-link ${isActive ? 'is-active' : ''}`} title="Workspace settings"><Settings2 /><span>Settings</span></NavLink>
        <a className="rail-link" href="mailto:support@northstar.ai" title="Help"><CircleHelp /><span>Help</span></a>
        <button className="rail-link rail-toggle" onClick={() => setExpanded((value) => !value)} title={expanded ? 'Collapse sidebar' : 'Expand sidebar'}>{expanded ? <PanelLeftClose /> : <PanelLeftOpen />}<span>{expanded ? 'Collapse' : 'Expand'}</span></button>
      </div>
    </aside>
    <div className="app-main">
      <header className="topbar">
        <div className="topbar__left"><div className="mobile-brand"><Brand /></div><div><span className="topbar__eyebrow">Workspace</span><h1>{pageTitle(location.pathname)}</h1></div></div>
        <div className="topbar__actions">
          <button className="search-trigger" aria-label="Search"><Search /><span>Search</span><kbd>⌘ K</kbd></button>
          <button className="icon-button" aria-label="Notifications"><Bell /><span className="notification-dot" /></button>
          <div className="account-menu">
            <button className="account-button" onClick={() => setAccountOpen((value) => !value)} aria-expanded={accountOpen}><span className="avatar">{initials}</span><span className="account-copy"><strong>{session?.user.name}</strong><small>{session?.user.role}</small></span><ChevronDown /></button>
            {accountOpen ? <div className="account-popover"><div><strong>{session?.user.name}</strong><small>{session?.user.email}</small></div><Link to="/settings" onClick={() => setAccountOpen(false)}><Settings2 />Workspace settings</Link><button onClick={() => void logout().then(() => navigate('/login'))}><LogOut />Sign out</button></div> : null}
          </div>
        </div>
      </header>
      <main className="content"><Outlet /></main>
    </div>
    <nav className="mobile-nav" aria-label="Mobile navigation">
      {navigation.slice(0, 5).map(({ to, label, icon: Icon, end }) => <NavLink key={to} to={to} end={end} aria-label={label}><Icon /><span>{label}</span></NavLink>)}
    </nav>
    <button className="command-fab" aria-label="Open command palette"><Command /></button>
  </div>;
}
