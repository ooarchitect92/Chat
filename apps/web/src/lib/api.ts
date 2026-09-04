import { demoAgents, demoAnalytics, demoConversations, demoIntegrations, demoKnowledge, demoLeads } from '@/lib/demo-data';
import { clone, readStorage, writeStorage } from '@/lib/storage';
import type {
  Agent, AgentPatch, AnalyticsSummary, ChatStreamEvent, ChatStreamRequest, Conversation, ConversationState,
  CompleteWhatsAppSignupInput, CreateAgentInput, Integration, KnowledgeKind, KnowledgeSource, Lead, PageResult, Session,
  WhatsAppBootstrap, WhatsAppConnection, WhatsAppStatus, WidgetBootstrap, WidgetSession,
} from '@/types';

const API_URL = (import.meta.env.VITE_API_URL as string | undefined)?.replace(/\/$/, '') ?? '/api/v1';
const DEMO_MODE = (import.meta.env.VITE_DEMO_MODE as string | undefined) !== 'false';
const TOKEN_KEY = 'northstar.session';
const AGENTS_KEY = 'northstar.agents';
const KNOWLEDGE_KEY = 'northstar.knowledge';
const CONVERSATIONS_KEY = 'northstar.conversations';
const LEADS_KEY = 'northstar.leads';
export const AUTH_SESSION_EVENT = 'northstar:session-changed';

interface ApiRequestInit extends RequestInit {
  skipAuth?: boolean;
  skipRefresh?: boolean;
}

let refreshFlight: { token: string; promise: Promise<Session> } | null = null;

export class ApiError extends Error {
  constructor(message: string, readonly status: number, readonly detail?: unknown) { super(message); this.name = 'ApiError'; }
}

const pause = (ms = 220) => new Promise((resolve) => setTimeout(resolve, ms));
const id = (prefix: string) => `${prefix}-${crypto.randomUUID?.() ?? Math.random().toString(36).slice(2)}`;

function session(): Session | null { return readStorage<Session | null>(TOKEN_KEY, null); }

function notifySessionChanged(): void {
  window.dispatchEvent(new Event(AUTH_SESSION_EVENT));
}

function browserSession(value: Session): Session {
  const safe = { ...value };
  delete safe.refreshToken;
  return safe;
}

function persistSession(value: Session): Session {
  const safe = browserSession(value);
  writeStorage(TOKEN_KEY, safe);
  notifySessionChanged();
  return safe;
}

function clearSession(expectedCredential?: string): void {
  const current = session();
  if (
    expectedCredential
    && current?.refreshToken !== expectedCredential
    && current?.accessToken !== expectedCredential
  ) return;
  localStorage.removeItem(TOKEN_KEY);
  notifySessionChanged();
}

async function refreshSession(active: Session): Promise<Session> {
  const refreshToken = active.refreshToken;
  const refreshIdentity = refreshToken ?? `http-only-cookie:${active.accessToken}`;
  if (refreshFlight?.token === refreshIdentity) return refreshFlight.promise;

  const promise = (async () => {
    try {
      const response = await fetch(`${API_URL}/auth/refresh`, {
        method: 'POST',
        credentials: 'include',
        headers: { Accept: 'application/json', ...(refreshToken ? { 'Content-Type': 'application/json' } : {}) },
        body: refreshToken ? JSON.stringify({ refreshToken }) : undefined,
      });
      if (!response.ok) throw new ApiError('Your session has expired. Please sign in again.', 401);
      const rotated = await response.json() as Session;
      const current = session();
      const stillCurrent = refreshToken
        ? current?.refreshToken === refreshToken
        : current?.accessToken === active.accessToken;
      if (!current || !stillCurrent) {
        if (current) return current;
        throw new ApiError('Your session has expired. Please sign in again.', 401);
      }
      return persistSession(rotated);
    } catch (error) {
      clearSession(refreshToken ?? active.accessToken);
      if (error instanceof ApiError) throw error;
      throw new ApiError('Your session has expired. Please sign in again.', 401, error);
    }
  })();

  refreshFlight = { token: refreshIdentity, promise };
  try {
    return await promise;
  } finally {
    if (refreshFlight?.promise === promise) refreshFlight = null;
  }
}

async function request<T>(path: string, options: ApiRequestInit = {}): Promise<T> {
  const { skipAuth = false, skipRefresh = false, ...init } = options;
  const active = session();
  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    credentials: init.credentials ?? 'include',
    headers: { Accept: 'application/json', ...(init.body ? { 'Content-Type': 'application/json' } : {}), ...(!skipAuth && active ? { Authorization: `Bearer ${active.accessToken}` } : {}), ...init.headers },
  });
  if (response.status === 401 && !skipAuth && !skipRefresh && active) {
    await refreshSession(active);
    return request<T>(path, { ...init, skipRefresh: true });
  }
  if (!response.ok) {
    let rawBody = '';
    try { rawBody = await response.text(); } catch { /* Preserve the HTTP status even if the body stream fails. */ }
    let detail: unknown = rawBody || undefined;
    if (rawBody) {
      try { detail = JSON.parse(rawBody) as unknown; } catch { /* Plain-text proxy and upstream errors remain readable. */ }
    }
    throw new ApiError(`Request failed (${response.status})`, response.status, detail);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

async function withFallback<T>(remote: () => Promise<T>, fallback: () => Promise<T>): Promise<T> {
  if (DEMO_MODE) {
    try { return await remote(); } catch (error) {
      // Preserve actionable validation/auth failures from a running API. A 404 is
      // allowed to fall back because standalone Vite previews have no API route.
      if (error instanceof ApiError && [400, 401, 403, 409, 422].includes(error.status)) throw error;
      return fallback();
    }
  }
  return remote();
}

function demoAgentList(): Agent[] { return readStorage(AGENTS_KEY, clone(demoAgents)); }
function demoKnowledgeList(): KnowledgeSource[] { return readStorage(KNOWLEDGE_KEY, clone(demoKnowledge)); }
function demoConversationList(): Conversation[] { return readStorage(CONVERSATIONS_KEY, clone(demoConversations)); }
function demoLeadList(): Lead[] { return readStorage(LEADS_KEY, clone(demoLeads)); }

interface UploadPresignResponse {
  method: 'POST';
  url: string;
  objectKey: string;
  fields: Record<string, string>;
  expiresAt: string;
}

function uploadContentType(file: File): string {
  const extension = file.name.toLowerCase().split('.').pop();
  if (extension === 'pdf') return 'application/pdf';
  if (extension === 'docx') return 'application/vnd.openxmlformats-officedocument.wordprocessingml.document';
  if (extension === 'md' || extension === 'markdown') return 'text/markdown';
  return 'text/plain';
}

async function uploadKnowledgeFile(file: File): Promise<string> {
  const contentType = uploadContentType(file);
  const digest = await crypto.subtle.digest('SHA-256', await new Response(file).arrayBuffer());
  const checksumSha256 = Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('');
  const presign = await request<UploadPresignResponse>('/uploads/presign', {
    method: 'POST',
    body: JSON.stringify({ filename: file.name, contentType, sizeBytes: file.size, checksumSha256 }),
  });
  const form = new FormData();
  for (const [name, value] of Object.entries(presign.fields)) form.append(name, value);
  form.append('file', file);
  const uploaded = await fetch(presign.url, {
    method: presign.method,
    body: form,
  });
  if (!uploaded.ok) throw new ApiError(`File upload failed (${uploaded.status})`, uploaded.status);
  return presign.objectKey;
}

async function* parseChatEvents(response: Response): AsyncGenerator<ChatStreamEvent> {
  if (!response.ok || !response.body) throw new ApiError('Unable to start chat', response.status);
  const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = '';
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split('\n\n'); buffer = frames.pop() ?? '';
    for (const frame of frames) {
      const data = frame.split('\n').filter((line) => line.startsWith('data:')).map((line) => line.slice(5).trim()).join('\n');
      if (!data || data === '[DONE]') continue;
      yield JSON.parse(data) as ChatStreamEvent;
    }
  }
}

async function* demoChatEvents(question: string, currentConversationId?: string, signal?: AbortSignal): AsyncGenerator<ChatStreamEvent> {
  const conversationId = currentConversationId ?? id('conv');
  yield { type: 'start', conversationId, messageId: id('msg') };
  const answer = demoAnswer(question);
  for (const word of answer.split(' ')) {
    if (signal?.aborted) return;
    await pause(24 + Math.random() * 28);
    yield { type: 'token', content: `${word} ` };
  }
  yield { type: 'citation', title: 'Northstar knowledge base' };
  yield { type: 'done', conversationId };
}

export function agentFromWidgetBootstrap(value: WidgetBootstrap): Agent {
  const now = new Date().toISOString();
  return {
    id: value.agentId, publicId: value.publicId, name: value.name, avatar: value.avatar,
    description: 'Grounded AI assistant', instructions: '', status: 'active', tone: 'friendly', language: 'English',
    conversations: 0, resolutionRate: 0, knowledgeCount: 0, createdAt: now, lastUpdated: now,
    appearance: value.appearance,
    model: { provider: 'nvidia', model: 'nvidia/nemotron-3-ultra-550b-a55b', temperature: 1, topP: 0.95, maxTokens: 16384, enableThinking: true, citationMode: 'when-available' },
    security: { allowedDomains: [], rateLimitPerMinute: 30, collectEmail: value.collectEmail, maskSensitiveData: true, retentionDays: 90 },
  };
}

export const api = {
  auth: {
    session,
    async login(email: string, password: string): Promise<Session> {
      const result = await withFallback(() => request<Session>('/auth/login', { method: 'POST', body: JSON.stringify({ email, password }), skipAuth: true, skipRefresh: true }), async () => {
        await pause(450);
        if (!email.trim() || !password) throw new ApiError('Email and password are required.', 400);
        const result: Session = { accessToken: `demo.${btoa(email)}.token`, expiresAt: new Date(Date.now() + 86_400_000).toISOString(), user: { id: 'user-demo', name: email.split('@')[0]?.replace(/[._-]/g, ' ') || 'Northstar User', email, role: 'owner' } };
        return result;
      });
      return persistSession(result);
    },
    async logout(): Promise<void> {
      const active = session();
      const revoke = (value: Session | null) => request('/auth/logout', {
        method: 'POST',
        body: value?.refreshToken ? JSON.stringify({ refreshToken: value.refreshToken }) : undefined,
        skipRefresh: true,
      });
      try {
        if (!DEMO_MODE) {
          try {
            await revoke(active);
          } catch (error) {
            if (!(error instanceof ApiError) || error.status !== 401 || !active) throw error;
            await revoke(await refreshSession(active));
          }
        }
      } finally {
        clearSession();
      }
    },
  },
  agents: {
    list: () => withFallback(() => request<Agent[]>('/agents'), async () => { await pause(); return demoAgentList(); }),
    get: (agentId: string) => withFallback(() => request<Agent>(`/agents/${agentId}`), async () => { await pause(100); const found = demoAgentList().find((item) => item.id === agentId); if (!found) throw new ApiError('Agent not found', 404); return found; }),
    create: (input: CreateAgentInput) => withFallback(() => request<Agent>('/agents', { method: 'POST', body: JSON.stringify(input) }), async () => {
      await pause(); const base = clone(demoAgents[0]!); const createdId = id('agent'); const created: Agent = { ...base, id: createdId, publicId: id('public'), name: input.name, description: input.description, status: 'draft', conversations: 0, resolutionRate: 0, knowledgeCount: 0, createdAt: new Date().toISOString(), lastUpdated: new Date().toISOString() };
      const items = demoAgentList(); items.unshift(created); writeStorage(AGENTS_KEY, items); return created;
    }),
    update: (agentId: string, patch: AgentPatch) => withFallback(() => request<Agent>(`/agents/${agentId}`, { method: 'PATCH', body: JSON.stringify(patch) }), async () => {
      await pause(); const items = demoAgentList(); const index = items.findIndex((item) => item.id === agentId); if (index < 0) throw new ApiError('Agent not found', 404);
      const updated = { ...items[index]!, ...patch, lastUpdated: new Date().toISOString() }; items[index] = updated; writeStorage(AGENTS_KEY, items); return updated;
    }),
    remove: (agentId: string) => withFallback(() => request<void>(`/agents/${agentId}`, { method: 'DELETE' }), async () => { await pause(); writeStorage(AGENTS_KEY, demoAgentList().filter((item) => item.id !== agentId)); }),
  },
  knowledge: {
    list: (agentId: string) => withFallback(() => request<KnowledgeSource[]>(`/agents/${agentId}/knowledge`), async () => { await pause(); return demoKnowledgeList().filter((item) => item.agentId === agentId); }),
    add: (agentId: string, input: { name: string; kind: KnowledgeKind; url?: string; content?: string; file?: File }) => withFallback(async () => {
      const objectKey = input.file ? await uploadKnowledgeFile(input.file) : undefined;
      return request<KnowledgeSource>(`/agents/${agentId}/knowledge`, {
        method: 'POST',
        body: JSON.stringify({ name: input.name, kind: input.kind, url: input.url, content: input.content, objectKey }),
      });
    }, async () => {
      await pause(500); const source: KnowledgeSource = { id: id('ks'), agentId, name: input.name, kind: input.kind, url: input.url, content: input.content, status: 'ready', sizeLabel: input.kind === 'url' || input.kind === 'sitemap' ? '1 page' : input.kind === 'text' ? `${input.content?.split(/\s+/).length ?? 0} words` : input.file ? `${(input.file.size / 1_048_576).toFixed(1)} MB` : 'Uploaded', chunks: Math.max(1, Math.round((input.content?.length ?? input.file?.size ?? 700) / 600)), updatedAt: new Date().toISOString() };
      const items = demoKnowledgeList(); items.unshift(source); writeStorage(KNOWLEDGE_KEY, items); return source;
    }),
    remove: (sourceId: string) => withFallback(() => request<void>(`/knowledge/${sourceId}`, { method: 'DELETE' }), async () => { await pause(); writeStorage(KNOWLEDGE_KEY, demoKnowledgeList().filter((item) => item.id !== sourceId)); }),
  },
  conversations: {
    list: () => withFallback(() => request<PageResult<Conversation>>('/conversations'), async () => { await pause(); const items = demoConversationList(); return { items, total: items.length, page: 1, pageSize: 50 }; }),
    updateState: (conversationId: string, state: ConversationState) => withFallback(() => request<Conversation>(`/conversations/${conversationId}`, { method: 'PATCH', body: JSON.stringify({ state }) }), async () => { const items = demoConversationList(); const item = items.find((entry) => entry.id === conversationId); if (!item) throw new ApiError('Conversation not found', 404); item.state = state; writeStorage(CONVERSATIONS_KEY, items); return item; }),
    reply: (conversationId: string, content: string) => withFallback(() => request<Conversation['messages'][number]>(`/conversations/${conversationId}/messages`, { method: 'POST', body: JSON.stringify({ content }) }), async () => ({ id: id('agent'), role: 'agent', content, createdAt: new Date().toISOString() })),
  },
  leads: {
    list: () => withFallback(() => request<PageResult<Lead>>('/leads'), async () => { await pause(); const items = demoLeadList(); return { items, total: items.length, page: 1, pageSize: 50 }; }),
    updateStatus: (leadId: string, status: string) => withFallback(() => request<Lead>(`/leads/${leadId}`, { method: 'PATCH', body: JSON.stringify({ status }) }), async () => {
      await pause(); const items = demoLeadList(); const lead = items.find((item) => item.id === leadId); if (!lead) throw new ApiError('Lead not found', 404);
      lead.status = status; lead.updatedAt = new Date().toISOString(); writeStorage(LEADS_KEY, items); return lead;
    }),
  },
  analytics: { summary: () => withFallback(() => request<AnalyticsSummary>('/analytics/summary'), async () => { await pause(); return clone(demoAnalytics); }) },
  integrations: {
    list: () => withFallback(() => request<Integration[]>('/integrations'), async () => { await pause(); return clone(demoIntegrations); }),
    setConnected: (integrationId: string, connected: boolean) => withFallback(() => request<Integration>(`/integrations/${integrationId}`, { method: 'PATCH', body: JSON.stringify({ connected }) }), async () => { await pause(); const result = clone(demoIntegrations.find((item) => item.id === integrationId)!); result.connected = connected; return result; }),
    whatsapp: {
      bootstrap: () => request<WhatsAppBootstrap>('/integrations/whatsapp/bootstrap'),
      status: () => request<WhatsAppStatus>('/integrations/whatsapp/status'),
      complete: (input: CompleteWhatsAppSignupInput) => request<WhatsAppConnection>('/integrations/whatsapp/complete', {
        method: 'POST', body: JSON.stringify(input),
      }),
      disconnect: () => request<void>('/integrations/whatsapp', { method: 'DELETE' }),
    },
  },
  widget: {
    bootstrap: (publicId: string) => withFallback(async () => agentFromWidgetBootstrap(await request<WidgetBootstrap>(`/widget/${publicId}/bootstrap`, {
      skipAuth: true,
      skipRefresh: true,
    })), async () => {
      await pause(120);
      const found = demoAgentList().find((item) => item.publicId === publicId || item.id === publicId);
      if (!found) throw new ApiError('Published agent not found', 404);
      return found;
    }),
    hostedBootstrap: (publicId: string) => withFallback(async () => agentFromWidgetBootstrap(await request<WidgetBootstrap>(`/widget/${publicId}/hosted/bootstrap`, {
      skipAuth: true,
      skipRefresh: true,
    })), async () => {
      await pause(120);
      const found = demoAgentList().find((item) => item.publicId === publicId || item.id === publicId);
      if (!found) throw new ApiError('Published agent not found', 404);
      return found;
    }),
    createSession: (publicId: string) => withFallback(() => request<WidgetSession>(`/widget/${publicId}/sessions`, {
      method: 'POST', body: JSON.stringify({ pageUrl: window.location.href }), skipAuth: true, skipRefresh: true,
    }), async () => ({ conversationId: id('conv'), conversationPublicId: id('public-conv'), sessionToken: id('widget-token'), expiresAt: new Date(Date.now() + 3_600_000).toISOString() })),
    createHostedSession: (publicId: string) => withFallback(() => request<WidgetSession>(`/widget/${publicId}/hosted/sessions`, {
      method: 'POST', body: JSON.stringify({ pageUrl: window.location.href }), skipAuth: true, skipRefresh: true,
    }), async () => ({ conversationId: id('conv'), conversationPublicId: id('public-conv'), sessionToken: id('widget-token'), expiresAt: new Date(Date.now() + 3_600_000).toISOString() })),
    async *streamChat(input: { conversationId: string; sessionToken: string; message: string }, signal?: AbortSignal): AsyncGenerator<ChatStreamEvent> {
      try {
        const response = await fetch(`${API_URL}/widget/sessions/${input.conversationId}/messages`, {
          method: 'POST', signal,
          headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream', Authorization: `Bearer ${input.sessionToken}` },
          body: JSON.stringify({ message: input.message, idempotencyKey: crypto.randomUUID?.() ?? id('turn') }),
        });
        yield* parseChatEvents(response);
        return;
      } catch (error) {
        if (signal?.aborted) return;
        if (!DEMO_MODE) { yield { type: 'error', message: error instanceof Error ? error.message : 'Chat unavailable' }; return; }
      }
      yield* demoChatEvents(input.message, input.conversationId, signal);
    },
  },
  async *streamChat(input: ChatStreamRequest, signal?: AbortSignal): AsyncGenerator<ChatStreamEvent> {
    try {
      const active = session();
      const response = await fetch(`${API_URL}/chat/stream`, { method: 'POST', signal, headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream', ...(active ? { Authorization: `Bearer ${active.accessToken}` } : {}) }, body: JSON.stringify(input) });
      yield* parseChatEvents(response);
      return;
    } catch (error) {
      if (signal?.aborted) return;
      if (!DEMO_MODE) { yield { type: 'error', message: error instanceof Error ? error.message : 'Chat unavailable' }; return; }
    }
    yield* demoChatEvents(input.message, input.conversationId, signal);
  },
};

function demoAnswer(question: string): string {
  const value = question.toLowerCase();
  if (value.includes('price') || value.includes('plan')) return 'Our plans scale with your team and conversation volume. The Growth plan adds analytics, multiple agents, and shared inboxes. I can help narrow it down if you tell me your team size.';
  if (value.includes('contact') || value.includes('support')) return 'You can reach the support team from the Help menu or ask me to escalate this conversation. A teammate will receive the transcript, so you will not need to repeat yourself.';
  if (value.includes('service') || value.includes('offer')) return 'Northstar helps teams create accurate AI support agents, train them on trusted sources, review conversations, and deploy them to websites or connected channels.';
  return 'I found the most relevant guidance in the connected knowledge base. Based on your question, the best next step is to open the agent workspace, confirm its knowledge sources are current, and test the answer in the live preview. Would you like the steps?';
}

export const apiConfig = { baseUrl: API_URL, demoMode: DEMO_MODE } as const;
