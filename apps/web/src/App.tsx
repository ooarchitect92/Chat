import { Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { AppShell } from '@/components/app-shell';
import { useAuth } from '@/components/providers';
import { AgentBuilderPage } from '@/pages/AgentBuilderPage';
import { AgentsPage } from '@/pages/AgentsPage';
import { AnalyticsPage } from '@/pages/AnalyticsPage';
import { ConversationsPage } from '@/pages/ConversationsPage';
import { DeployPage } from '@/pages/DeployPage';
import { IntegrationsPage } from '@/pages/IntegrationsPage';
import { LeadsPage } from '@/pages/LeadsPage';
import { LoginPage } from '@/pages/LoginPage';
import { NotFoundPage } from '@/pages/NotFoundPage';
import { OverviewPage } from '@/pages/OverviewPage';
import { SettingsPage } from '@/pages/SettingsPage';
import { WidgetDemoPage } from '@/pages/WidgetDemoPage';

function ProtectedLayout() {
  const { session } = useAuth(); const location = useLocation();
  if (!session) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  return <AppShell />;
}

export function App() {
  return <Routes>
    <Route path="/login" element={<LoginPage />} />
    <Route path="/demo/:agentId?" element={<WidgetDemoPage />} />
    <Route path="/widget/:agentId" element={<WidgetDemoPage embedded />} />
    <Route element={<ProtectedLayout />}>
      <Route index element={<OverviewPage />} />
      <Route path="agents" element={<AgentsPage />} />
      <Route path="agents/:agentId/:tab?" element={<AgentBuilderPage />} />
      <Route path="conversations" element={<ConversationsPage />} />
      <Route path="leads" element={<LeadsPage />} />
      <Route path="analytics" element={<AnalyticsPage />} />
      <Route path="integrations" element={<IntegrationsPage />} />
      <Route path="deploy" element={<DeployPage />} />
      <Route path="settings" element={<SettingsPage />} />
    </Route>
    <Route path="*" element={<NotFoundPage />} />
  </Routes>;
}
