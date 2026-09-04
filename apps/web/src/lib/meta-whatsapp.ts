interface FacebookLoginResponse {
  authResponse?: { code?: string };
  status?: string;
}

interface FacebookSdk {
  init: (options: { appId: string; autoLogAppEvents: boolean; cookie: boolean; xfbml: boolean; version: string }) => void;
  login: (
    callback: (response: FacebookLoginResponse) => void,
    options: {
      config_id: string;
      response_type: 'code';
      override_default_response_type: true;
      extras: {
        sessionInfoVersion: '3';
        version: 'v4';
        setup: Record<string, never>;
      };
    },
  ) => void;
}

declare global {
  interface Window {
    FB?: FacebookSdk;
    fbAsyncInit?: () => void;
  }
}

export interface MetaSignupResult {
  code: string;
  wabaId: string;
  phoneNumberId: string;
}

export interface MetaSignupConfiguration {
  appId: string;
  configurationId: string;
  apiVersion: string;
}

export class MetaSignupError extends Error {
  constructor(message: string, readonly reason: 'cancelled' | 'failed' | 'timeout' | 'expired' | 'aborted' | 'configuration') {
    super(message);
    this.name = 'MetaSignupError';
  }
}

const SDK_ID = 'facebook-jssdk';
const SDK_URL = 'https://connect.facebook.net/en_US/sdk.js';
const META_MESSAGE_ORIGINS = new Set([
  'https://www.facebook.com',
  'https://web.facebook.com',
]);

let sdkFlight: Promise<FacebookSdk> | null = null;

function validateConfiguration(configuration: Pick<MetaSignupConfiguration, 'appId' | 'apiVersion'>): void {
  if (!configuration.appId.trim() || !/^v\d+\.\d+$/.test(configuration.apiVersion)) {
    throw new MetaSignupError('The Meta application configuration is incomplete.', 'configuration');
  }
}

function initialiseSdk(sdk: FacebookSdk, configuration: Pick<MetaSignupConfiguration, 'appId' | 'apiVersion'>): FacebookSdk {
  sdk.init({ appId: configuration.appId, autoLogAppEvents: true, cookie: true, xfbml: false, version: configuration.apiVersion });
  return sdk;
}

export function loadFacebookSdk(configuration: Pick<MetaSignupConfiguration, 'appId' | 'apiVersion'>): Promise<FacebookSdk> {
  validateConfiguration(configuration);
  if (window.FB) return Promise.resolve(initialiseSdk(window.FB, configuration));
  if (sdkFlight) return sdkFlight.then((sdk) => initialiseSdk(sdk, configuration));

  sdkFlight = new Promise<FacebookSdk>((resolve, reject) => {
    let settled = false;
    let script: HTMLScriptElement | null = null;
    const previousInitialiser = window.fbAsyncInit;
    const rejectLoad = (error: MetaSignupError) => {
      if (settled) return;
      settled = true;
      window.clearTimeout(timeout);
      if (window.fbAsyncInit === finish) window.fbAsyncInit = previousInitialiser;
      if (script && !window.FB) script.remove();
      sdkFlight = null;
      reject(error);
    };
    const finish = () => {
      if (settled) return;
      if (!window.FB) {
        rejectLoad(new MetaSignupError('Meta Login could not be loaded.', 'failed'));
        return;
      }
      settled = true;
      window.clearTimeout(timeout);
      if (window.fbAsyncInit === finish) window.fbAsyncInit = previousInitialiser;
      try {
        previousInitialiser?.();
        resolve(initialiseSdk(window.FB, configuration));
      } catch (error) {
        sdkFlight = null;
        reject(error);
      }
    };
    const timeout = window.setTimeout(() => rejectLoad(new MetaSignupError('Meta Login took too long to load. Please try again.', 'timeout')), 20_000);
    window.fbAsyncInit = finish;

    const existing = document.getElementById(SDK_ID) as HTMLScriptElement | null;
    if (existing) {
      script = existing;
      existing.addEventListener('load', finish, { once: true });
      existing.addEventListener('error', () => rejectLoad(new MetaSignupError('Meta Login could not be loaded. Check your connection and try again.', 'failed')), { once: true });
      return;
    }

    script = document.createElement('script');
    script.id = SDK_ID;
    script.src = SDK_URL;
    script.async = true;
    script.defer = true;
    script.crossOrigin = 'anonymous';
    script.addEventListener('error', () => rejectLoad(new MetaSignupError('Meta Login could not be loaded. Check your connection and try again.', 'failed')), { once: true });
    document.head.appendChild(script);
  });

  return sdkFlight;
}

interface EmbeddedSignupMessage {
  type: 'WA_EMBEDDED_SIGNUP';
  event: 'FINISH' | 'CANCEL' | 'ERROR';
  data?: Record<string, unknown>;
}

function parseEmbeddedSignupMessage(value: unknown): EmbeddedSignupMessage | null {
  let candidate: unknown = value;
  if (typeof value === 'string') {
    try { candidate = JSON.parse(value) as unknown; } catch { return null; }
  }
  if (!candidate || typeof candidate !== 'object') return null;
  const record = candidate as Record<string, unknown>;
  if (record.type !== 'WA_EMBEDDED_SIGNUP' || !['FINISH', 'CANCEL', 'ERROR'].includes(String(record.event))) return null;
  return candidate as EmbeddedSignupMessage;
}

function stringField(record: Record<string, unknown> | undefined, snakeCase: string, camelCase: string): string | null {
  const value = record?.[snakeCase] ?? record?.[camelCase];
  return typeof value === 'string' && value.trim() ? value : null;
}

export function startWhatsAppEmbeddedSignup(
  configuration: MetaSignupConfiguration,
  signal?: AbortSignal,
  timeoutMs = 5 * 60_000,
  codeMaxAgeMs = 25_000,
): Promise<MetaSignupResult> {
  if (!configuration.configurationId.trim()) {
    throw new MetaSignupError('The WhatsApp signup configuration is incomplete.', 'configuration');
  }
  if (signal?.aborted) throw new MetaSignupError('WhatsApp signup was closed.', 'aborted');
  validateConfiguration(configuration);
  // Embedded Signup opens a popup, so FB.login must run in the original click
  // event. The modal preloads the SDK before enabling its continue button.
  const sdk = window.FB;
  if (!sdk) throw new MetaSignupError('Meta Login is still loading. Please wait a moment and try again.', 'failed');
  initialiseSdk(sdk, configuration);

  return new Promise<MetaSignupResult>((resolve, reject) => {
    let code: string | null = null;
    let wabaId: string | null = null;
    let phoneNumberId: string | null = null;
    let settled = false;
    let codeExpiry: number | null = null;

    const cleanup = () => {
      window.removeEventListener('message', onMessage);
      signal?.removeEventListener('abort', onAbort);
      window.clearTimeout(timeout);
      if (codeExpiry !== null) window.clearTimeout(codeExpiry);
    };
    const fail = (error: MetaSignupError) => {
      if (settled) return;
      settled = true;
      cleanup();
      reject(error);
    };
    const finishWhenReady = () => {
      if (settled || !code || !wabaId || !phoneNumberId) return;
      settled = true;
      cleanup();
      resolve({ code, wabaId, phoneNumberId });
    };
    const onAbort = () => fail(new MetaSignupError('WhatsApp signup was closed.', 'aborted'));
    const onMessage = (event: MessageEvent<unknown>) => {
      if (!META_MESSAGE_ORIGINS.has(event.origin)) return;
      const message = parseEmbeddedSignupMessage(event.data);
      if (!message) return;
      if (message.event === 'CANCEL') {
        fail(new MetaSignupError('WhatsApp signup was cancelled before it finished.', 'cancelled'));
        return;
      }
      if (message.event === 'ERROR') {
        fail(new MetaSignupError('Meta could not finish WhatsApp signup. Please review the account details and try again.', 'failed'));
        return;
      }
      wabaId = stringField(message.data, 'waba_id', 'wabaId');
      phoneNumberId = stringField(message.data, 'phone_number_id', 'phoneNumberId');
      if (!wabaId || !phoneNumberId) {
        fail(new MetaSignupError('Meta returned an incomplete WhatsApp account selection.', 'failed'));
        return;
      }
      finishWhenReady();
    };
    const timeout = window.setTimeout(() => fail(new MetaSignupError('WhatsApp signup timed out. Please start again.', 'timeout')), timeoutMs);

    window.addEventListener('message', onMessage);
    signal?.addEventListener('abort', onAbort, { once: true });
    try {
      sdk.login((response) => {
        const authorizationCode = response.authResponse?.code;
        if (!authorizationCode) {
          fail(new MetaSignupError('Facebook login was not completed.', 'cancelled'));
          return;
        }
        if (code) return;
        code = authorizationCode;
        codeExpiry = window.setTimeout(() => fail(new MetaSignupError('The Meta authorization code expired before account selection finished. Please start again.', 'expired')), codeMaxAgeMs);
        finishWhenReady();
      }, {
        config_id: configuration.configurationId,
        response_type: 'code',
        override_default_response_type: true,
        extras: {
          sessionInfoVersion: '3',
          version: 'v4',
          setup: {},
        },
      });
    } catch {
      fail(new MetaSignupError('Facebook login could not be opened. Allow pop-ups and try again.', 'failed'));
    }
  });
}
