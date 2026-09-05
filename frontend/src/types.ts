export type AgentStatus = 'active' | 'draft' | 'training' | 'error';
export type AgentTone = 'professional' | 'friendly' | 'concise' | 'empathetic' | 'playful';
export type KnowledgeKind = 'file' | 'url' | 'text' | 'sitemap';
export type KnowledgeStatus = 'ready' | 'processing' | 'failed';

export interface User {
  id: string;
  name: string;
  email: string;
  role: 'owner' | 'admin' | 'member' | 'analyst';
  avatarUrl?: string;
}

export interface Session {
  accessToken: string;
  refreshToken?: string;
  user: User;
  expiresAt: string;
}

export interface AgentAppearance {
  primaryColor: string;
  surfaceColor: string;
  position: 'bottom-right' | 'bottom-left';
  launcherStyle: 'spark' | 'bubble' | 'avatar';
  welcomeTitle: string;
  welcomeMessage: string;
  placeholder: string;
  suggestedQuestions: string[];
  showBranding: boolean;
}

export interface AgentModel {
  provider: 'nvidia';
  model: string;
  temperature: number;
  topP: number;
  maxTokens: number;
  enableThinking: boolean;
  citationMode: 'always' | 'when-available' | 'off';
}

export interface AgentSecurity {
  allowedDomains: string[];
  rateLimitPerMinute: number;
  collectEmail: boolean;
  maskSensitiveData: boolean;
  retentionDays: number;
}

export interface Agent {
  id: string;
  publicId: string;
  name: string;
  description: string;
  instructions: string;
  status: AgentStatus;
  tone: AgentTone;
  language: string;
  avatar: string;
  conversations: number;
  resolutionRate: number;
  knowledgeCount: number;
  lastUpdated: string;
  createdAt: string;
  appearance: AgentAppearance;
  model: AgentModel;
  security: AgentSecurity;
}

export interface KnowledgeSource {
  id: string;
  agentId: string;
  name: string;
  kind: KnowledgeKind;
  status: KnowledgeStatus;
  sizeLabel: string;
  chunks: number;
  updatedAt: string;
  url?: string;
  content?: string;
  error?: string;
}

export type ConversationState = 'open' | 'resolved' | 'escalated';

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'agent' | 'system';
  content: string;
  createdAt: string;
  citations?: Array<{ title: string; url?: string }>;
}

export interface Conversation {
  id: string;
  agentId: string;
  visitorName: string;
  visitorEmail?: string;
  channel: 'widget' | 'api' | 'slack' | 'whatsapp';
  state: ConversationState;
  sentiment: 'positive' | 'neutral' | 'negative';
  preview: string;
  unread: number;
  startedAt: string;
  updatedAt: string;
  messages: ChatMessage[];
}

export interface AnalyticsSummary {
  period: string;
  conversations: number;
  conversationsDelta: number;
  resolutionRate: number;
  resolutionDelta: number;
  avgResponseSeconds: number;
  satisfaction: number;
  chart: Array<{ label: string; conversations: number; resolved: number }>;
  topQuestions: Array<{ question: string; count: number; resolutionRate: number }>;
  channels: Array<{ channel: string; value: number }>;
}

export interface Integration {
  id: string;
  name: string;
  description: string;
  category: 'channel' | 'automation' | 'data' | 'developer';
  icon: string;
  connected: boolean;
  comingSoon?: boolean;
}

export interface WhatsAppConnection {
  wabaId: string;
  phoneNumberId: string;
  displayPhoneNumber: string;
  verifiedName: string;
  agentId: string;
  status: string;
  tokenExpiresAt?: string | null;
  connectedAt: string;
}

export interface WhatsAppBootstrap {
  appId: string | null;
  configurationId: string | null;
  signupSession: string | null;
  apiVersion: string;
  enabled: boolean;
  connected: boolean;
  connection: WhatsAppConnection | null;
  connections?: WhatsAppConnection[];
}

export interface WhatsAppStatus {
  enabled: boolean;
  connected: boolean;
  connection: WhatsAppConnection | null;
  connections?: WhatsAppConnection[];
}

export interface CompleteWhatsAppSignupInput {
  code: string;
  wabaId: string;
  phoneNumberId: string;
  agentId: string;
  signupSession: string;
  twoStepVerificationPin: string;
}

export interface Lead {
  id: string;
  agentId: string;
  conversationId?: string;
  name?: string;
  email?: string;
  phone?: string;
  status: string;
  consent: boolean;
  fields: Record<string, unknown>;
  createdAt: string;
  updatedAt: string;
}

export interface PageResult<T> {
  items: T[];
  total: number;
  page: number;
  pageSize: number;
}

export interface ChatStreamRequest {
  agentId: string;
  message: string;
  conversationId?: string;
  visitorId?: string;
}

export interface WidgetBootstrap {
  agentId: string;
  publicId: string;
  name: string;
  avatar: string;
  appearance: AgentAppearance;
  collectEmail: boolean;
  sessionEndpoint: string;
  streamEndpoint: string;
}

export interface WidgetSession {
  conversationId: string;
  conversationPublicId: string;
  sessionToken: string;
  expiresAt: string;
}

export interface WidgetInitMessage {
  type: 'northstar:init';
  agentId: string;
  bootstrap: WidgetBootstrap;
  session: WidgetSession;
}

export interface WidgetReadyMessage {
  type: 'northstar:ready';
  agentId: string;
}

export interface WidgetSessionRequestMessage {
  type: 'northstar:session-request';
  agentId: string;
  requestId: string;
}

export interface WidgetSessionMessage {
  type: 'northstar:session';
  agentId: string;
  requestId: string;
  session: WidgetSession;
}

export interface WidgetSessionErrorMessage {
  type: 'northstar:session-error';
  agentId: string;
  requestId: string;
  message: string;
}

export type ChatStreamEvent =
  | { type: 'start'; conversationId: string; messageId: string }
  | { type: 'token'; content: string }
  | { type: 'citation'; title: string; url?: string }
  | { type: 'done'; conversationId: string }
  | { type: 'error'; message: string };

export interface CreateAgentInput {
  name: string;
  description: string;
  template?: string;
}

export type AgentPatch = Partial<Omit<Agent, 'id' | 'createdAt'>>;
