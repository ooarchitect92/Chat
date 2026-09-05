import { ArrowUp, ExternalLink, MessageSquareText, Mic, MoreVertical, RefreshCw, ShieldCheck, Sparkles, X } from 'lucide-react';
import { useEffect, useRef, useState, type FormEvent } from 'react';
import { api } from '@/lib/api';
import type { Agent, ChatMessage, WidgetSession } from '@/types';

interface WidgetProps { agent: Agent; embedded?: boolean; startOpen?: boolean; publicMode?: boolean; initialSession?: WidgetSession; requestNewSession?: () => Promise<WidgetSession>; onClose?: () => void }

export function ChatWidget({ agent, embedded = false, startOpen = true, publicMode = false, initialSession, requestNewSession, onClose }: WidgetProps) {
  const [open, setOpen] = useState(startOpen || embedded); const [input, setInput] = useState(''); const [messages, setMessages] = useState<ChatMessage[]>([]); const [streaming, setStreaming] = useState(false); const [resetting, setResetting] = useState(false); const [conversationId, setConversationId] = useState<string | undefined>(initialSession?.conversationId); const [widgetToken, setWidgetToken] = useState<string | undefined>(initialSession?.sessionToken);
  const scrollRef = useRef<HTMLDivElement>(null); const controller = useRef<AbortController>();
  useEffect(() => { scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' }); }, [messages, streaming]);
  useEffect(() => () => controller.current?.abort(), []);
  const reset = async () => {
    controller.current?.abort(); setMessages([]); setConversationId(undefined); setWidgetToken(undefined); setStreaming(false);
    if (!requestNewSession) return;
    setResetting(true);
    try {
      const created = await requestNewSession();
      setConversationId(created.conversationId); setWidgetToken(created.sessionToken);
    } catch {
      setMessages([{ id: `session-error-${Date.now()}`, role: 'assistant', content: 'I could not start a new conversation. Please try again.', createdAt: new Date().toISOString() }]);
    } finally {
      setResetting(false);
    }
  };
  const send = async (value: string) => {
    const content = value.trim(); if (!content || streaming) return; setInput('');
    const userMessage: ChatMessage = { id: `local-${Date.now()}`, role: 'user', content, createdAt: new Date().toISOString() };
    const responseId = `response-${Date.now()}`;
    setMessages((current) => [...current, userMessage, { id: responseId, role: 'assistant', content: '', createdAt: new Date().toISOString(), citations: [] }]); setStreaming(true);
    controller.current = new AbortController();
    try {
      let activeConversationId = conversationId; let activeWidgetToken = widgetToken;
      if (publicMode && (!activeConversationId || !activeWidgetToken)) {
        const created = requestNewSession ? await requestNewSession() : await api.widget.createSession(agent.publicId);
        activeConversationId = created.conversationId; activeWidgetToken = created.sessionToken;
        setConversationId(activeConversationId); setWidgetToken(activeWidgetToken);
      }
      const events = publicMode && activeConversationId && activeWidgetToken
        ? api.widget.streamChat({ conversationId: activeConversationId, sessionToken: activeWidgetToken, message: content }, controller.current.signal)
        : api.streamChat({ agentId: agent.id, message: content, conversationId }, controller.current.signal);
      for await (const event of events) {
        if (event.type === 'start') setConversationId(event.conversationId);
        if (event.type === 'token') setMessages((current) => current.map((message) => message.id === responseId ? { ...message, content: message.content + event.content } : message));
        if (event.type === 'citation') setMessages((current) => current.map((message) => message.id === responseId ? { ...message, citations: [...(message.citations ?? []), { title: event.title, url: event.url }] } : message));
        if (event.type === 'error') setMessages((current) => current.map((message) => message.id === responseId ? { ...message, content: `I’m sorry, I couldn’t complete that response. ${event.message}` } : message));
      }
    } catch (error) {
      setMessages((current) => current.map((message) => message.id === responseId ? { ...message, content: `I could not complete that response. ${error instanceof Error ? error.message : 'Please try again.'}` } : message));
    } finally { setStreaming(false); }
  };
  const submit = (event: FormEvent) => { event.preventDefault(); void send(input); };
  if (!open && !embedded) return <button className="widget-launcher" style={{ background: agent.appearance.primaryColor }} onClick={() => setOpen(true)} aria-label={`Open chat with ${agent.name}`}>{agent.appearance.launcherStyle === 'bubble' ? <MessageSquareText /> : agent.appearance.launcherStyle === 'avatar' ? <span className="widget-launcher__avatar">{agent.avatar.slice(0, 2)}</span> : <Sparkles />}</button>;
  return <section className={`chat-widget ${embedded ? 'chat-widget--embedded' : ''}`} style={{ '--widget-primary': agent.appearance.primaryColor, '--widget-surface': agent.appearance.surfaceColor } as React.CSSProperties} aria-label={`Chat with ${agent.name}`}>
    <header className="widget-header"><div className="widget-wordmark"><span className="widget-logo"><Sparkles /></span><strong>northstar<span>ai</span></strong></div><div><button onClick={() => void reset()} disabled={resetting || streaming} aria-label="Start a new conversation" title="New conversation"><RefreshCw /></button><button aria-label="More options"><MoreVertical /></button>{!embedded && onClose ? <button onClick={onClose} aria-label="Close chat"><X /></button> : null}</div></header>
    <div className="widget-body" ref={scrollRef}>
      {messages.length === 0 ? <div className="widget-welcome"><span className="widget-orb"><Sparkles /></span><h2>{agent.appearance.welcomeTitle}</h2><p>{agent.appearance.welcomeMessage}</p><div className="suggestion-list">{agent.appearance.suggestedQuestions.filter(Boolean).map((question) => <button key={question} onClick={() => void send(question)}>{question}<ArrowUp /></button>)}</div></div> : <div className="message-list"><div className="message-day"><span>Today</span></div>{messages.map((message, index) => <div key={message.id} className={`chat-message chat-message--${message.role}`}>
        {message.role === 'assistant' ? <span className="message-avatar"><Sparkles /></span> : null}<div className="message-bubble">{message.content || (streaming && index === messages.length - 1 ? <span className="typing"><i/><i/><i/></span> : null)}{message.citations?.length ? <div className="citations">{message.citations.map((citation) => <span key={citation.title}><ExternalLink /> {citation.title}</span>)}</div> : null}</div>
      </div>)}</div>}
    </div>
    <footer className="widget-footer"><form onSubmit={submit}><textarea rows={1} value={input} onChange={(event) => setInput(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); void send(input); } }} placeholder={agent.appearance.placeholder} aria-label="Message" /><div className="composer-actions"><button type="button" aria-label="Voice input" title="Voice input"><Mic /></button><span /><button type="submit" className="send-button" disabled={!input.trim() || streaming} aria-label="Send message"><ArrowUp /></button></div></form><p><ShieldCheck /> AI can make mistakes. Check important information.</p>{agent.appearance.showBranding ? <small>Powered by <strong><Sparkles /> Northstar AI</strong></small> : null}</footer>
  </section>;
}

export function WidgetPreview({ agent }: { agent: Agent }) {
  return <aside className="widget-preview"><div className="preview-toolbar"><span><i className="status-dot status-dot--success" /> Live preview</span><button aria-label="Open preview options"><MoreVertical /></button></div><div className="phone-frame"><div className="phone-speaker" /><ChatWidget agent={agent} embedded /></div><p>Changes appear here before you publish.</p></aside>;
}
