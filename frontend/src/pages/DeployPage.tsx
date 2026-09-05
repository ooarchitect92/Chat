import {
  Bot,
  Check,
  ChevronDown,
  Code2,
  Copy,
  Download,
  ExternalLink,
  FileCode2,
  Globe2,
  Image,
  Languages,
  Link2,
  MessageSquareText,
  Monitor,
  Paintbrush,
  PanelBottom,
  Plus,
  QrCode,
  RotateCcw,
  Save,
  ShieldCheck,
  SlidersHorizontal,
  Smartphone,
  Sparkles,
  ToggleRight,
  Type,
  WandSparkles,
} from 'lucide-react';
import { useEffect, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { ChatWidget } from '@/components/chat-widget';
import { useToast } from '@/components/providers';
import { Badge, Button, Card, Field, PageLoader, Switch } from '@/components/ui';
import { api } from '@/lib/api';
import { createQrAssets, downloadQrAsset, type QrAssets } from '@/lib/qr';
import { useApi } from '@/lib/use-api';
import type { Agent, AgentAppearance, AgentPatch } from '@/types';

const buildItems = [
  { id: 'source', label: 'Agent source', icon: Bot },
  { id: 'look', label: 'Look and feel', icon: Paintbrush },
  { id: 'toggle', label: 'Toggle', icon: ToggleRight },
  { id: 'starters', label: 'Conversation starters', icon: MessageSquareText },
  { id: 'color', label: 'Color studio', icon: WandSparkles },
  { id: 'font', label: 'Font studio', icon: Type },
  { id: 'greeting', label: 'Greeting editor', icon: Sparkles },
  { id: 'localization', label: 'Localization', icon: Languages },
  { id: 'gdpr', label: 'GDPR & consent', icon: ShieldCheck },
  { id: 'other', label: 'Other', icon: SlidersHorizontal },
] as const;

const shareItems = [
  { id: 'link', label: 'Get link', icon: Link2 },
  { id: 'qr', label: 'QR code', icon: QrCode },
  { id: 'embed', label: 'Instant embed', icon: Code2 },
  { id: 'iframe', label: 'IFrame embed', icon: FileCode2 },
] as const;

type DeploySection = (typeof buildItems)[number]['id'] | (typeof shareItems)[number]['id'];

const persistedSections = new Set<DeploySection>(['look', 'toggle', 'starters', 'color', 'greeting', 'other']);

function cloneAgent(agent: Agent): Agent {
  return {
    ...agent,
    appearance: { ...agent.appearance, suggestedQuestions: [...agent.appearance.suggestedQuestions] },
    model: { ...agent.model },
    security: { ...agent.security, allowedDomains: [...agent.security.allowedDomains] },
  };
}

function savedDeployState(agent: Agent): Pick<Agent, 'status' | 'appearance'> {
  return { status: agent.status, appearance: agent.appearance };
}

function normalizeAppearance(appearance: AgentAppearance): AgentAppearance {
  return {
    ...appearance,
    primaryColor: appearance.primaryColor.toLowerCase(),
    suggestedQuestions: appearance.suggestedQuestions
      .map((question) => question.trim())
      .filter(Boolean)
      .slice(0, 8),
  };
}

function isValidAppearance(appearance: AgentAppearance): boolean {
  return /^#[0-9a-f]{6}$/i.test(appearance.primaryColor)
    && appearance.welcomeTitle.length <= 120
    && appearance.welcomeMessage.length <= 500
    && appearance.placeholder.length <= 120
    && appearance.suggestedQuestions.length <= 8;
}

export function DeployPage() {
  const agents = useApi(() => api.agents.list());
  const [agentId, setAgentId] = useState('agent-northstar');
  const [savedAgents, setSavedAgents] = useState<Agent[]>([]);
  const [draftAgents, setDraftAgents] = useState<Agent[]>([]);
  const [section, setSection] = useState<DeploySection>('look');
  const [device, setDevice] = useState<'desktop' | 'mobile'>('mobile');
  const [saving, setSaving] = useState(false);
  const { pushToast } = useToast();

  useEffect(() => {
    if (!agents.data?.length) return;
    const next = agents.data.map(cloneAgent);
    setSavedAgents(next);
    setDraftAgents(next.map(cloneAgent));
    setAgentId((current) => next.some((agent) => agent.id === current) ? current : next[0]!.id);
  }, [agents.data]);

  const agent = draftAgents.find((item) => item.id === agentId) ?? draftAgents[0];
  const savedAgent = savedAgents.find((item) => item.id === agent?.id);
  const dirty = Boolean(agent && savedAgent && JSON.stringify(savedDeployState(agent)) !== JSON.stringify(savedDeployState(savedAgent)));
  const valid = Boolean(agent && isValidAppearance(agent.appearance));
  const showActions = persistedSections.has(section);

  const updateAgent = (patch: AgentPatch) => {
    if (!agent) return;
    setDraftAgents((current) => current.map((item) => item.id === agent.id ? { ...item, ...patch } : item));
  };

  const updateAppearance = (patch: Partial<AgentAppearance>) => {
    if (!agent) return;
    updateAgent({ appearance: { ...agent.appearance, ...patch } });
  };

  const reset = () => {
    if (!agent || !savedAgent) return;
    setDraftAgents((current) => current.map((item) => item.id === agent.id ? cloneAgent(savedAgent) : item));
    pushToast('Widget changes reset', 'info');
  };

  const save = async () => {
    if (!agent || !valid || !dirty) return;
    setSaving(true);
    try {
      const saved = await api.agents.update(agent.id, {
        appearance: normalizeAppearance(agent.appearance),
        status: agent.status,
      });
      const next = cloneAgent(saved);
      setSavedAgents((current) => current.map((item) => item.id === saved.id ? next : item));
      setDraftAgents((current) => current.map((item) => item.id === saved.id ? cloneAgent(next) : item));
      pushToast('Widget changes published');
    } catch (reason) {
      pushToast(reason instanceof Error ? reason.message : 'Could not publish widget changes', 'error');
    } finally {
      setSaving(false);
    }
  };

  if (agents.loading || !agent) return <PageLoader />;

  return <div className="deploy-page">
    <aside className="deploy-sidebar">
      <div className="deploy-sidebar__heading">
        <span className="agent-avatar" style={{ background: agent.appearance.primaryColor }}>{agent.avatar}</span>
        <span><small>My widget</small><strong>{agent.name}</strong></span>
        <ChevronDown />
      </div>
      <p>BUILD</p>
      {buildItems.map(({ id, label, icon: Icon }) => <button key={id} className={section === id ? 'is-active' : ''} onClick={() => setSection(id)}><Icon /><span>{label}</span></button>)}
      <p>SHARE</p>
      {shareItems.map(({ id, label, icon: Icon }) => <button key={id} className={section === id ? 'is-active' : ''} onClick={() => setSection(id)}><Icon /><span>{label}</span></button>)}
    </aside>

    <section className="deploy-editor">
      <div className="deploy-editor__heading">
        <div>
          <span className="page-kicker">Widget manager</span>
          <h2>{[...buildItems, ...shareItems].find((item) => item.id === section)?.label}</h2>
          <p>Customize and publish a polished experience for every visitor.</p>
        </div>
        {showActions ? <div className="deploy-heading-actions">
          <Button variant="ghost" icon={RotateCcw} disabled={!dirty || saving} onClick={reset}>Reset changes</Button>
          <Button icon={Save} disabled={!dirty || saving || !valid} onClick={() => void save()}>{saving ? 'Applying...' : 'Apply'}</Button>
        </div> : null}
      </div>
      <DeployEditor
        key={agent.id}
        section={section}
        agent={agent}
        agents={draftAgents}
        setAgentId={setAgentId}
        updateAgent={updateAgent}
        updateAppearance={updateAppearance}
      />
    </section>

    <aside className="deploy-preview">
      <div className="preview-heading">
        <span><i className="status-dot status-dot--success" /> Interactive preview</span>
        <div className="device-toggle">
          <button className={device === 'desktop' ? 'is-active' : ''} onClick={() => setDevice('desktop')} aria-label="Desktop preview"><Monitor /></button>
          <button className={device === 'mobile' ? 'is-active' : ''} onClick={() => setDevice('mobile')} aria-label="Mobile preview"><Smartphone /></button>
        </div>
      </div>
      <div className={`deploy-preview__stage deploy-preview__stage--${device}`}>
        <div className="browser-mock">
          <div className="browser-mock__bar"><i /><i /><i /><span>yourwebsite.com</span></div>
          <div className="browser-mock__page"><div className="mock-nav" /><div className="mock-hero"><i /><i /><i /></div><div className="mock-cards"><i /><i /><i /></div></div>
          <div className={`embedded-widget embedded-widget--${agent.appearance.position}`}><ChatWidget agent={agent} embedded /></div>
        </div>
      </div>
      <p className="preview-note">Preview scales to fit. Your live widget remains fully responsive.</p>
    </aside>
  </div>;
}

interface DeployEditorProps {
  section: DeploySection;
  agent: Agent;
  agents: Agent[];
  setAgentId: (id: string) => void;
  updateAgent: (patch: AgentPatch) => void;
  updateAppearance: (patch: Partial<AgentAppearance>) => void;
}

function DeployEditor({ section, agent, agents, setAgentId, updateAgent, updateAppearance }: DeployEditorProps) {
  const [openDefault, setOpenDefault] = useState(false);
  const [direction, setDirection] = useState('ltr');
  const [greetingMode, setGreetingMode] = useState('once');
  const [consent, setConsent] = useState(false);
  const [zIndex, setZIndex] = useState(99999);
  const [font, setFont] = useState('Inter');
  const [radius, setRadius] = useState(24);
  const [width, setWidth] = useState(380);
  const [height, setHeight] = useState(680);
  const [shadow, setShadow] = useState(true);
  const [margin, setMargin] = useState(24);
  const [customDomain, setCustomDomain] = useState('');
  const [iframeWidth, setIframeWidth] = useState(100);
  const [iframeHeight, setIframeHeight] = useState(700);
  const hostedUrl = `${window.location.origin}/demo/${agent.publicId}`;
  const embeddedUrl = `${window.location.origin}/widget/${agent.publicId}`;

  if (section === 'source') return <Panel title="Choose an agent" description="This widget uses the selected agent's knowledge, behavior, and model.">
    <Field label="Agent source" htmlFor="deploy-agent">
      <select id="deploy-agent" value={agent.id} onChange={(event) => setAgentId(event.target.value)}>{agents.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select>
    </Field>
    <div className="selected-agent">
      <span className="agent-avatar" style={{ background: agent.appearance.primaryColor }}>{agent.avatar}</span>
      <span><strong>{agent.name}</strong><small>{agent.knowledgeCount} sources &middot; {agent.status}</small></span>
      <Badge tone="success">Connected</Badge>
    </div>
    <Link className="inline-link" to={`/agents/${agent.id}/instructions`}>Edit agent <ExternalLink /></Link>
  </Panel>;

  if (section === 'look') return <>
    <Panel title="Widget dimensions" description="Set the expanded size. Mobile automatically fills available width.">
      <div className="two-fields"><NumberField label="Width" value={width} onChange={setWidth} suffix="px" min={320} max={600} /><NumberField label="Height" value={height} onChange={setHeight} suffix="px" min={480} max={900} /></div>
      <div className="segmented-field"><span>Placement</span><div>
        <button className={agent.appearance.position === 'bottom-left' ? 'is-active' : ''} onClick={() => updateAppearance({ position: 'bottom-left' })}>Bottom left</button>
        <button className={agent.appearance.position === 'bottom-right' ? 'is-active' : ''} onClick={() => updateAppearance({ position: 'bottom-right' })}>Bottom right</button>
      </div></div>
      <div className="two-fields"><NumberField label="Page margin" value={margin} onChange={setMargin} suffix="px" min={8} max={64} /><Field label="Text direction" htmlFor="direction"><select id="direction" value={direction} onChange={(event) => setDirection(event.target.value)}><option value="ltr">Left to right</option><option value="rtl">Right to left</option></select></Field></div>
      <Switch label="Elevated shadow" description="Separate the widget from page content." checked={shadow} onChange={setShadow} />
    </Panel>
    <Panel title="Interface copy" description="Fine-tune high-visibility widget text.">
      <Field label="Hero heading" htmlFor="look-hero"><input id="look-hero" maxLength={120} value={agent.appearance.welcomeTitle} onChange={(event) => updateAppearance({ welcomeTitle: event.target.value })} /></Field>
      <Field label="Input placeholder" htmlFor="look-placeholder"><input id="look-placeholder" maxLength={120} value={agent.appearance.placeholder} onChange={(event) => updateAppearance({ placeholder: event.target.value })} /></Field>
      <Field label="AI warning" htmlFor="look-warning" hint="This safety disclosure is managed by the widget runtime."><input id="look-warning" value="AI can make mistakes. Check important information." disabled /></Field>
    </Panel>
  </>;

  if (section === 'toggle') return <Panel title="Launcher behavior" description="Control how and when the widget appears.">
    <Switch label="Widget enabled" description="Show the launcher on connected websites." checked={agent.status === 'active'} onChange={(enabled) => updateAgent({ status: enabled ? 'active' : 'draft' })} />
    <Switch label="Open on page load" description="Preview this installation-level option before embedding." checked={openDefault} onChange={setOpenDefault} />
    <div className="launcher-options"><strong>Launcher style</strong><div>
      <button className={agent.appearance.launcherStyle === 'spark' ? 'is-active' : ''} onClick={() => updateAppearance({ launcherStyle: 'spark' })}><Sparkles />Spark</button>
      <button className={agent.appearance.launcherStyle === 'bubble' ? 'is-active' : ''} onClick={() => updateAppearance({ launcherStyle: 'bubble' })}><MessageSquareText />Bubble</button>
      <button className={agent.appearance.launcherStyle === 'avatar' ? 'is-active' : ''} onClick={() => updateAppearance({ launcherStyle: 'avatar' })}><Image />Avatar</button>
    </div></div>
  </Panel>;

  if (section === 'starters') return <Panel title="Conversation starters" description="Help visitors begin with your most useful questions.">
    <div className="editable-list">{agent.appearance.suggestedQuestions.map((question, index) => <div key={index}>
      <span>{index + 1}</span>
      <input aria-label={`Conversation starter ${index + 1}`} maxLength={180} value={question} onChange={(event) => {
        const suggestedQuestions = [...agent.appearance.suggestedQuestions];
        suggestedQuestions[index] = event.target.value;
        updateAppearance({ suggestedQuestions });
      }} />
      <button aria-label={`Remove conversation starter ${index + 1}`} onClick={() => updateAppearance({ suggestedQuestions: agent.appearance.suggestedQuestions.filter((_, itemIndex) => itemIndex !== index) })}>&times;</button>
    </div>)}</div>
    <Button variant="secondary" icon={Plus} disabled={agent.appearance.suggestedQuestions.length >= 8} onClick={() => updateAppearance({ suggestedQuestions: [...agent.appearance.suggestedQuestions, ''] })}>Add starter</Button>
  </Panel>;

  if (section === 'color') {
    const colorValid = /^#[0-9a-f]{6}$/i.test(agent.appearance.primaryColor);
    return <Panel title="Color studio" description="Set an accessible palette that matches your brand.">
      <div className="palette-preview"><i style={{ background: colorValid ? agent.appearance.primaryColor : '#146cf6' }} /><i style={{ background: '#07111f' }} /><i style={{ background: '#f6f8fb' }} /><i style={{ background: '#ffffff' }} /></div>
      <Field label="Primary color" htmlFor="deploy-color" error={colorValid ? undefined : 'Enter a six-digit hex color, for example #146cf6.'}><div className="color-input">
        <input type="color" id="deploy-color" value={colorValid ? agent.appearance.primaryColor : '#146cf6'} onChange={(event) => updateAppearance({ primaryColor: event.target.value })} />
        <input aria-label="Primary color hex" value={agent.appearance.primaryColor} onChange={(event) => updateAppearance({ primaryColor: event.target.value })} />
      </div></Field>
      <div className="preset-colors">{['#146cf6', '#705cf6', '#0f9f84', '#e5484d', '#df6c1b', '#111827'].map((color) => <button key={color} style={{ background: color }} onClick={() => updateAppearance({ primaryColor: color })} aria-label={`Use color ${color}`} />)}</div>
      <div className="contrast-check"><Check /><span><strong>High contrast</strong><small>Button labels remain easy to read.</small></span></div>
    </Panel>;
  }

  if (section === 'font') return <Panel title="Font studio" description="Choose typography that feels native to your website.">
    <Field label="Font family" htmlFor="font-family"><select id="font-family" value={font} onChange={(event) => setFont(event.target.value)}><option>Inter</option><option>DM Sans</option><option>Manrope</option><option>System UI</option></select></Field>
    <NumberField label="Corner radius" value={radius} onChange={setRadius} suffix="px" min={0} max={32} />
    <div className="font-sample" style={{ fontFamily: font, borderRadius: radius }}><small>FONT PREVIEW</small><h3>{agent.appearance.welcomeTitle}</h3><p>{agent.appearance.welcomeMessage}</p><button style={{ background: agent.appearance.primaryColor }}>Start a conversation</button></div>
  </Panel>;

  if (section === 'greeting') return <Panel title="Greeting editor" description="Welcome visitors at the right moment without being intrusive.">
    <Field label="Greeting" htmlFor="greeting"><textarea id="greeting" rows={4} maxLength={500} value={agent.appearance.welcomeMessage} onChange={(event) => updateAppearance({ welcomeMessage: event.target.value })} /></Field>
    <fieldset className="greeting-modes"><legend>Show greeting</legend>{[['always', 'Always'], ['once', 'Once per session'], ['interaction', 'Until interaction'], ['never', 'Never']].map(([value, label]) => <label key={value} className={greetingMode === value ? 'is-selected' : ''}>
      <input type="radio" name="greeting-mode" checked={greetingMode === value} onChange={() => setGreetingMode(value!)} />
      <span><strong>{label}</strong><small>{value === 'always' ? 'Every page load' : value === 'once' ? 'Once until the browser closes' : value === 'interaction' ? 'Until the visitor opens chat' : 'Launcher only'}</small></span>
    </label>)}</fieldset>
  </Panel>;

  if (section === 'localization') return <Panel title="Localization" description="Translate interface labels while the agent answers in the visitor's language.">
    <Field label="Interface language" htmlFor="ui-language"><select id="ui-language" defaultValue="English"><option>English</option><option>Hindi</option><option>Spanish</option><option>French</option><option>Arabic</option></select></Field>
    <div className="localization-fields"><Field label="New conversation"><input defaultValue="New conversation" /></Field><Field label="Send button"><input defaultValue="Send" /></Field><Field label="Close chat"><input defaultValue="Close chat" /></Field><Field label="Offline message"><input defaultValue="We'll be back soon" /></Field></div>
    <Switch label="Detect browser language" description="Use a saved translation when one is available." checked onChange={() => undefined} />
  </Panel>;

  if (section === 'gdpr') return <Panel title="GDPR & consent" description="Give visitors clear control before collecting personal data.">
    <Switch label="Require consent" description="Visitors must agree before starting a conversation." checked={consent} onChange={setConsent} />
    {consent ? <><Field label="Consent message" htmlFor="consent-message"><textarea id="consent-message" rows={4} defaultValue="I agree that my messages may be processed to answer my request." /></Field><Field label="Privacy policy URL" htmlFor="privacy-url"><input id="privacy-url" type="url" placeholder="https://example.com/privacy" /></Field></> : null}
    <div className="privacy-note"><ShieldCheck /><span><strong>Privacy by design</strong><small>Sensitive data masking and retention controls are configured on the agent.</small></span></div>
  </Panel>;

  if (section === 'other') return <Panel title="Advanced options" description="Fine-grained identity and display controls.">
    <Switch label="Northstar branding" description="Show Powered by Northstar AI below the composer." checked={agent.appearance.showBranding} onChange={(showBranding) => updateAppearance({ showBranding })} />
    <Field label="Internal widget name" htmlFor="widget-name"><input id="widget-name" defaultValue={`${agent.name} widget`} /></Field>
    <NumberField label="Layer (z-index)" value={zIndex} onChange={setZIndex} min={1} max={2147483647} />
    <div className="readonly-field"><span><strong>Widget ID</strong><small>Immutable installation identifier</small></span><code>wdg_{agent.id.replace('agent-', '')}</code></div>
  </Panel>;

  if (section === 'link') return <SharePanel icon={Link2} title="Hosted widget link" description="Share a full-page version in emails, documents, or social posts.">
    <div className="share-url"><Globe2 /><span>{hostedUrl}</span><CopyAction value={hostedUrl} /></div>
    <div className="share-actions"><Link to={`/demo/${agent.publicId}`} target="_blank" rel="noreferrer"><Button icon={ExternalLink}>Open demo</Button></Link></div>
    <hr />
    <Field label="Custom domain" htmlFor="custom-domain" hint="Create a CNAME record after saving."><input id="custom-domain" value={customDomain} onChange={(event) => setCustomDomain(event.target.value)} placeholder="ask.yourcompany.com" /></Field>
    <Button variant="secondary" disabled={!customDomain}>Connect domain</Button>
  </SharePanel>;

  if (section === 'qr') return <QrSharePanel hostedUrl={hostedUrl} agent={agent} />;

  if (section === 'embed') {
    const code = `<script src="${window.location.origin}/widget.js" data-agent-id="${agent.publicId}" async></script>`;
    return <SharePanel icon={Code2} title="Instant embed" description="Paste this before the closing </body> tag on every page where chat should appear.">
      <CodeSnippet value={code} />
      <div className="connected-domains"><strong>Allowed domains</strong>{agent.security.allowedDomains.map((domain) => <span key={domain}><i className="status-dot status-dot--success" /> {domain} <Badge tone="success">Allowed</Badge></span>)}</div>
      <div className="recovery-tip"><PanelBottom /><span><strong>Widget not appearing?</strong><small>Check the browser console, domain allowlist, and content security policy.</small></span><button>Run diagnostics</button></div>
    </SharePanel>;
  }

  const iframe = `<iframe src="${embeddedUrl}" title="Chat with ${agent.name}" width="${iframeWidth}%" height="${iframeHeight}" frameborder="0" allow="clipboard-write"></iframe>`;
  return <SharePanel icon={FileCode2} title="IFrame embed" description="Place a complete chat experience inside a page, portal, or app.">
    <CodeSnippet value={iframe} />
    <div className="two-fields"><NumberField label="Width" value={iframeWidth} onChange={setIframeWidth} suffix="%" min={20} max={100} /><NumberField label="Height" value={iframeHeight} onChange={setIframeHeight} suffix="px" min={480} max={1200} /></div>
  </SharePanel>;
}

function QrSharePanel({ hostedUrl, agent }: { hostedUrl: string; agent: Agent }) {
  const [assets, setAssets] = useState<QrAssets>();
  const [error, setError] = useState('');
  const { pushToast } = useToast();

  useEffect(() => {
    let active = true;
    setAssets(undefined);
    setError('');
    void createQrAssets(hostedUrl)
      .then((result) => { if (active) setAssets(result); })
      .catch(() => { if (active) setError('Could not generate this QR code. Please try again.'); });
    return () => { active = false; };
  }, [hostedUrl]);

  const download = (format: 'png' | 'svg') => {
    if (!assets) return;
    downloadQrAsset(assets, format, agent.publicId);
    pushToast(`QR code downloaded as ${format.toUpperCase()}`);
  };

  return <SharePanel icon={QrCode} title="QR code" description="Send visitors directly to your hosted agent from print or physical spaces.">
    <div className="qr-layout">
      <div className="qr-code-preview" aria-live="polite">
        {assets ? <img src={assets.pngDataUrl} alt={`QR code linking to ${hostedUrl}`} /> : <span>{error ? <QrCode /> : 'Generating...'}</span>}
      </div>
      <div>
        <strong>{error ? 'QR unavailable' : assets ? 'Ready to scan' : 'Preparing QR code'}</strong>
        <p>{error || 'High contrast with a quiet zone for reliable scanning.'}</p>
        <Button icon={Download} disabled={!assets} onClick={() => download('png')}>Download PNG</Button>
        <Button variant="secondary" icon={Download} disabled={!assets} onClick={() => download('svg')}>Download SVG</Button>
      </div>
    </div>
  </SharePanel>;
}

function Panel({ title, description, children }: { title: string; description: string; children: ReactNode }) {
  return <Card className="deploy-panel"><div className="deploy-panel__heading"><h3>{title}</h3><p>{description}</p></div><div className="deploy-panel__body">{children}</div></Card>;
}

function SharePanel({ icon: Icon, title, description, children }: { icon: typeof Link2; title: string; description: string; children: ReactNode }) {
  return <Card className="share-panel"><div className="share-panel__hero"><span><Icon /></span><h3>{title}</h3><p>{description}</p></div><div className="share-panel__body">{children}</div></Card>;
}

function NumberField({ label, value, onChange, suffix, min, max }: { label: string; value: number; onChange: (value: number) => void; suffix?: string; min: number; max: number }) {
  return <Field label={label}><div className="number-field"><input type="number" value={value} min={min} max={max} onChange={(event) => onChange(Number(event.target.value))} />{suffix ? <span>{suffix}</span> : null}</div></Field>;
}

function CopyAction({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    await navigator.clipboard.writeText(value);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1400);
  };
  return <button onClick={() => void copy()}>{copied ? <Check /> : <Copy />}{copied ? 'Copied' : 'Copy'}</button>;
}

function CodeSnippet({ value }: { value: string }) {
  return <div className="large-code"><pre><code>{value}</code></pre><CopyAction value={value} /></div>;
}
