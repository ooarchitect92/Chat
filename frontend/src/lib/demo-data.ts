import type { Agent, AnalyticsSummary, Conversation, Integration, KnowledgeSource, Lead } from '@/types';

const now = new Date();
const isoAgo = (days = 0, hours = 0) => new Date(now.getTime() - days * 86_400_000 - hours * 3_600_000).toISOString();

export const demoAgents: Agent[] = [
  {
    id: 'agent-northstar',
    publicId: 'northstar-guide',
    name: 'Northstar Guide',
    description: 'Customer support and product expert',
    instructions: `You are Northstar Guide, a precise and approachable AI support agent. Answer using the connected knowledge base before relying on general knowledge.\n\nStart with the direct answer, then add helpful context. Keep responses concise, friendly, and professional. If the evidence is incomplete, say so clearly and offer to connect the visitor with a person. Never invent policies, pricing, or product capabilities.\n\nCite the relevant source whenever one is available.`,
    status: 'active', tone: 'friendly', language: 'English', avatar: 'N', conversations: 1284,
    resolutionRate: 87, knowledgeCount: 12, lastUpdated: isoAgo(0, 2), createdAt: isoAgo(48),
    appearance: {
      primaryColor: '#146cf6', surfaceColor: '#f6f8fb', position: 'bottom-right', launcherStyle: 'spark',
      welcomeTitle: 'How can I help you?', welcomeMessage: 'Ask a question and I’ll find the most useful answer.',
      placeholder: 'Ask me anything…', suggestedQuestions: ['What services do you offer?', 'How can I contact support?', 'Tell me about your plans.'], showBranding: true,
    },
    model: { provider: 'nvidia', model: 'nvidia/nemotron-3-ultra-550b-a55b', temperature: 0.7, topP: 0.95, maxTokens: 4096, enableThinking: true, citationMode: 'when-available' },
    security: { allowedDomains: ['localhost', 'northstar.example'], rateLimitPerMinute: 30, collectEmail: false, maskSensitiveData: true, retentionDays: 90 },
  },
  {
    id: 'agent-sales', publicId: 'sales-concierge', name: 'Sales Concierge', description: 'Qualifies leads and recommends plans', instructions: 'Help visitors choose the right plan. Ask one useful qualifying question at a time.', status: 'active', tone: 'professional', language: 'English', avatar: 'S', conversations: 608, resolutionRate: 82, knowledgeCount: 8, lastUpdated: isoAgo(1), createdAt: isoAgo(32),
    appearance: { primaryColor: '#705cf6', surfaceColor: '#f7f6ff', position: 'bottom-right', launcherStyle: 'bubble', welcomeTitle: 'Find your perfect plan', welcomeMessage: 'Tell me what you want to accomplish.', placeholder: 'Describe your goals…', suggestedQuestions: ['Compare plans', 'Book a demo', 'Do you support teams?'], showBranding: true },
    model: { provider: 'nvidia', model: 'nvidia/nemotron-3-ultra-550b-a55b', temperature: 0.65, topP: 0.9, maxTokens: 4096, enableThinking: true, citationMode: 'always' },
    security: { allowedDomains: [], rateLimitPerMinute: 20, collectEmail: true, maskSensitiveData: true, retentionDays: 90 },
  },
  {
    id: 'agent-onboarding', publicId: 'onboarding-coach', name: 'Onboarding Coach', description: 'Guides customers through first steps', instructions: 'Guide users through onboarding with short numbered steps.', status: 'draft', tone: 'empathetic', language: 'English', avatar: 'O', conversations: 0, resolutionRate: 0, knowledgeCount: 3, lastUpdated: isoAgo(4), createdAt: isoAgo(7),
    appearance: { primaryColor: '#0f9f84', surfaceColor: '#f2fbf8', position: 'bottom-left', launcherStyle: 'spark', welcomeTitle: 'Let’s get you started', welcomeMessage: 'I can guide you through setup.', placeholder: 'What are you setting up?', suggestedQuestions: ['Create my first agent', 'Add knowledge', 'Invite my team'], showBranding: true },
    model: { provider: 'nvidia', model: 'nvidia/nemotron-3-ultra-550b-a55b', temperature: 0.6, topP: 0.9, maxTokens: 4096, enableThinking: true, citationMode: 'when-available' },
    security: { allowedDomains: [], rateLimitPerMinute: 30, collectEmail: false, maskSensitiveData: true, retentionDays: 30 },
  },
];

export const demoKnowledge: KnowledgeSource[] = [
  { id: 'ks-1', agentId: 'agent-northstar', name: 'Product handbook.pdf', kind: 'file', status: 'ready', sizeLabel: '2.4 MB', chunks: 86, updatedAt: isoAgo(0, 3) },
  { id: 'ks-2', agentId: 'agent-northstar', name: 'Help Center', kind: 'sitemap', status: 'ready', sizeLabel: '48 pages', chunks: 214, updatedAt: isoAgo(1), url: 'https://docs.northstar.example/sitemap.xml' },
  { id: 'ks-3', agentId: 'agent-northstar', name: 'Pricing & plans', kind: 'url', status: 'ready', sizeLabel: '1 page', chunks: 18, updatedAt: isoAgo(2), url: 'https://northstar.example/pricing' },
  { id: 'ks-4', agentId: 'agent-northstar', name: 'Support escalation policy', kind: 'text', status: 'ready', sizeLabel: '1,120 words', chunks: 12, updatedAt: isoAgo(5), content: 'Escalate billing disputes and security concerns to a human specialist.' },
];

export const demoConversations: Conversation[] = [
  { id: 'conv-1', agentId: 'agent-northstar', visitorName: 'Maya Chen', visitorEmail: 'maya@example.com', channel: 'widget', state: 'open', sentiment: 'positive', preview: 'That fixed it — thank you! One more question…', unread: 2, startedAt: isoAgo(0, 2), updatedAt: isoAgo(0, 0.08), messages: [
    { id: 'm1', role: 'user', content: 'How do I invite another teammate?', createdAt: isoAgo(0, 0.4) },
    { id: 'm2', role: 'assistant', content: 'Open Settings → Team, then select “Invite member.” Enter their email and choose a role. They’ll receive an invitation link valid for 7 days.', createdAt: isoAgo(0, 0.3), citations: [{ title: 'Team management guide' }] },
    { id: 'm3', role: 'user', content: 'That fixed it — thank you! One more question: can I change their role later?', createdAt: isoAgo(0, 0.08) },
  ] },
  { id: 'conv-2', agentId: 'agent-northstar', visitorName: 'Liam Brooks', channel: 'api', state: 'escalated', sentiment: 'negative', preview: 'I was charged twice for the same month.', unread: 1, startedAt: isoAgo(0, 6), updatedAt: isoAgo(0, 1), messages: [
    { id: 'm4', role: 'user', content: 'I was charged twice for the same month.', createdAt: isoAgo(0, 1) },
    { id: 'm5', role: 'assistant', content: 'I’m sorry about that. Billing disputes need a specialist, so I’ve escalated this conversation. You won’t need to repeat the details.', createdAt: isoAgo(0, 0.9) },
  ] },
  { id: 'conv-3', agentId: 'agent-sales', visitorName: 'Nora Reed', visitorEmail: 'nora@acme.test', channel: 'widget', state: 'resolved', sentiment: 'positive', preview: 'Perfect, the Growth plan sounds right.', unread: 0, startedAt: isoAgo(1), updatedAt: isoAgo(1), messages: [
    { id: 'm6', role: 'user', content: 'We have a support team of 12. Which plan should we use?', createdAt: isoAgo(1, 1) },
    { id: 'm7', role: 'assistant', content: 'For 12 teammates, the Growth plan is the best fit. It includes shared inboxes, analytics, and role-based access.', createdAt: isoAgo(1, 0.9) },
  ] },
  { id: 'conv-4', agentId: 'agent-northstar', visitorName: 'Anonymous visitor', channel: 'widget', state: 'resolved', sentiment: 'neutral', preview: 'Where can I export my data?', unread: 0, startedAt: isoAgo(2), updatedAt: isoAgo(2), messages: [{ id: 'm8', role: 'user', content: 'Where can I export my data?', createdAt: isoAgo(2) }] },
];

export const demoLeads: Lead[] = [
  { id: 'lead-1', agentId: 'agent-sales', conversationId: 'conv-3', name: 'Nora Reed', email: 'nora@acme.test', phone: '+1 415 555 0184', status: 'qualified', consent: true, fields: { company: 'Acme', teamSize: 12 }, createdAt: isoAgo(1), updatedAt: isoAgo(0, 8) },
  { id: 'lead-2', agentId: 'agent-northstar', conversationId: 'conv-1', name: 'Maya Chen', email: 'maya@example.com', status: 'contacted', consent: true, fields: { source: 'Help center' }, createdAt: isoAgo(2), updatedAt: isoAgo(1) },
  { id: 'lead-3', agentId: 'agent-sales', name: 'Arjun Mehta', email: 'arjun@northwind.test', phone: '+91 98765 43210', status: 'new', consent: true, fields: { company: 'Northwind' }, createdAt: isoAgo(3), updatedAt: isoAgo(3) },
  { id: 'lead-4', agentId: 'agent-onboarding', name: 'Sam Rivera', email: 'sam@example.net', status: 'converted', consent: true, fields: { plan: 'Growth' }, createdAt: isoAgo(7), updatedAt: isoAgo(2) },
  { id: 'lead-5', agentId: 'agent-sales', name: 'Anonymous visitor', phone: '+44 20 7946 0958', status: 'new', consent: false, fields: {}, createdAt: isoAgo(9), updatedAt: isoAgo(9) },
];

export const demoAnalytics: AnalyticsSummary = {
  period: 'Last 30 days', conversations: 1892, conversationsDelta: 18.4, resolutionRate: 86.7, resolutionDelta: 4.2,
  avgResponseSeconds: 1.8, satisfaction: 4.7,
  chart: [
    { label: 'Aug 05', conversations: 46, resolved: 37 }, { label: 'Aug 09', conversations: 58, resolved: 49 },
    { label: 'Aug 13', conversations: 51, resolved: 45 }, { label: 'Aug 17', conversations: 79, resolved: 66 },
    { label: 'Aug 21', conversations: 68, resolved: 60 }, { label: 'Aug 25', conversations: 94, resolved: 82 },
    { label: 'Aug 29', conversations: 85, resolved: 76 }, { label: 'Sep 02', conversations: 108, resolved: 96 },
  ],
  topQuestions: [
    { question: 'How do I invite teammates?', count: 146, resolutionRate: 94 },
    { question: 'Which plan is right for me?', count: 121, resolutionRate: 89 },
    { question: 'How can I update billing?', count: 88, resolutionRate: 81 },
    { question: 'Where can I export data?', count: 64, resolutionRate: 92 },
  ],
  channels: [{ channel: 'Web widget', value: 68 }, { channel: 'API', value: 19 }, { channel: 'Slack', value: 9 }, { channel: 'Other', value: 4 }],
};

export const demoIntegrations: Integration[] = [
  { id: 'website', name: 'Website widget', description: 'Add Northstar AI to any site with one snippet.', category: 'channel', icon: 'code', connected: true },
  { id: 'slack', name: 'Slack', description: 'Answer questions where your team already works.', category: 'channel', icon: 'hash', connected: false },
  { id: 'whatsapp', name: 'WhatsApp', description: 'Support customers through WhatsApp Business.', category: 'channel', icon: 'message', connected: false },
  { id: 'zapier', name: 'Zapier', description: 'Trigger workflows from conversations and events.', category: 'automation', icon: 'zap', connected: false },
  { id: 'notion', name: 'Notion', description: 'Continuously sync selected knowledge pages.', category: 'data', icon: 'book', connected: false },
  { id: 'api', name: 'Developer API', description: 'Build custom experiences with the REST and stream APIs.', category: 'developer', icon: 'terminal', connected: true },
  { id: 'teams', name: 'Microsoft Teams', description: 'Bring helpful answers into every channel.', category: 'channel', icon: 'users', connected: false, comingSoon: true },
];
