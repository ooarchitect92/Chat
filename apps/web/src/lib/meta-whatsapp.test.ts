import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  getFacebookLoginStatus, loadFacebookSdk, logoutFacebook, MetaSignupError, startWhatsAppEmbeddedSignup,
} from '@/lib/meta-whatsapp';

const configuration = { appId: '123456789', configurationId: '987654321', apiVersion: 'v26.0' };

describe('Meta WhatsApp Embedded Signup', () => {
  let loginCallback: ((response: { authResponse?: { code?: string } }) => void) | undefined;
  const init = vi.fn();
  const login = vi.fn((callback: (response: { authResponse?: { code?: string } }) => void) => { loginCallback = callback; });
  const getLoginStatus = vi.fn((callback: (response: { status?: 'connected' | 'not_authorized' | 'unknown' }) => void) => callback({ status: 'connected' }));
  const logout = vi.fn((callback: (response: { status?: 'connected' | 'not_authorized' | 'unknown' }) => void) => callback({ status: 'unknown' }));

  beforeEach(() => {
    window.FB = { init, login, getLoginStatus, logout };
    loginCallback = undefined;
    document.getElementById('facebook-jssdk')?.remove();
  });

  afterEach(() => {
    vi.useRealTimers();
    delete window.FB;
    delete window.fbAsyncInit;
    document.getElementById('facebook-jssdk')?.remove();
  });

  it('captures the one-time code and selected Cloud API number without accepting untrusted messages', async () => {
    const result = startWhatsAppEmbeddedSignup(configuration, undefined, 2_000);

    expect(login).toHaveBeenCalledWith(expect.any(Function), {
      config_id: configuration.configurationId,
      response_type: 'code',
      override_default_response_type: true,
      extras: {
        sessionInfoVersion: '3',
        version: 'v4',
        setup: {},
      },
    });
    expect(init).toHaveBeenCalledWith({
      appId: configuration.appId, autoLogAppEvents: true, cookie: true, xfbml: false, version: configuration.apiVersion,
    });
    loginCallback?.({ authResponse: { code: 'one-time-code' } });
    window.dispatchEvent(new MessageEvent('message', {
      origin: 'https://www.facebook.com.attacker.example',
      data: JSON.stringify({ type: 'WA_EMBEDDED_SIGNUP', event: 'FINISH', data: { waba_id: 'bad', phone_number_id: 'bad' } }),
    }));
    window.dispatchEvent(new MessageEvent('message', {
      origin: 'https://www.facebook.com',
      data: JSON.stringify({ type: 'WA_EMBEDDED_SIGNUP', event: 'FINISH', data: { waba_id: 'waba-1', phone_number_id: 'phone-1' } }),
    }));

    await expect(result).resolves.toEqual({ code: 'one-time-code', wabaId: 'waba-1', phoneNumberId: 'phone-1' });
  });

  it('cleans up and reports cancellation from Meta', async () => {
    const result = startWhatsAppEmbeddedSignup(configuration, undefined, 2_000);
    window.dispatchEvent(new MessageEvent('message', {
      origin: 'https://web.facebook.com',
      data: { type: 'WA_EMBEDDED_SIGNUP', event: 'CANCEL', data: { current_step: 'phone_number' } },
    }));

    await expect(result).rejects.toEqual(expect.objectContaining<Partial<MetaSignupError>>({ reason: 'cancelled' }));
  });

  it('also completes when Meta posts the number selection before returning its code', async () => {
    const result = startWhatsAppEmbeddedSignup(configuration, undefined, 2_000);
    window.dispatchEvent(new MessageEvent('message', {
      origin: 'https://www.facebook.com',
      data: JSON.stringify({ type: 'WA_EMBEDDED_SIGNUP', event: 'FINISH', data: { waba_id: 'waba-first', phone_number_id: 'phone-first' } }),
    }));
    loginCallback?.({ authResponse: { code: 'code-second' } });

    await expect(result).resolves.toEqual({ code: 'code-second', wabaId: 'waba-first', phoneNumberId: 'phone-first' });
  });

  it('expires an authorization code quickly if the number selection never arrives', async () => {
    vi.useFakeTimers();
    const result = startWhatsAppEmbeddedSignup(configuration, undefined, 2_000, 20);
    loginCallback?.({ authResponse: { code: 'short-lived-code' } });
    const rejection = expect(result).rejects.toEqual(expect.objectContaining<Partial<MetaSignupError>>({ reason: 'expired' }));
    await vi.advanceTimersByTimeAsync(21);

    await rejection;
  });

  it('stops waiting when the modal aborts signup', async () => {
    const controller = new AbortController();
    const result = startWhatsAppEmbeddedSignup(configuration, controller.signal, 2_000);
    controller.abort();

    await expect(result).rejects.toEqual(expect.objectContaining<Partial<MetaSignupError>>({ reason: 'aborted' }));
  });

  it('clears a failed SDK load so a retry can succeed', async () => {
    delete window.FB;
    const firstAttempt = loadFacebookSdk(configuration);
    const script = document.getElementById('facebook-jssdk');
    expect(script).not.toBeNull();
    script?.dispatchEvent(new Event('error'));
    await expect(firstAttempt).rejects.toEqual(expect.objectContaining<Partial<MetaSignupError>>({ reason: 'failed' }));
    expect(document.getElementById('facebook-jssdk')).toBeNull();

    window.FB = { init, login, getLoginStatus, logout };
    await expect(loadFacebookSdk(configuration)).resolves.toBe(window.FB);
  });

  it('reports and logs out the reusable Facebook browser session', async () => {
    await expect(getFacebookLoginStatus(configuration)).resolves.toBe('connected');
    await expect(logoutFacebook(configuration)).resolves.toBe('unknown');

    expect(getLoginStatus).toHaveBeenCalledWith(expect.any(Function), true);
    expect(logout).toHaveBeenCalledOnce();
  });
});
