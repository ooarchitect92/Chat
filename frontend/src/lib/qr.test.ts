import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createQrAssets, safeDownloadName } from '@/lib/qr';

const qrMocks = vi.hoisted(() => ({
  toDataURL: vi.fn<(...args: unknown[]) => Promise<string>>(),
  toString: vi.fn<(...args: unknown[]) => Promise<string>>(),
}));

vi.mock('qrcode', () => ({
  default: qrMocks,
}));

describe('QR assets', () => {
  beforeEach(() => {
    qrMocks.toDataURL.mockResolvedValue('data:image/png;base64,qr');
    qrMocks.toString.mockResolvedValue('<svg aria-label="qr" />');
  });

  it('generates both downloadable formats from the same destination', async () => {
    const destination = 'https://example.test/demo/agent-public';

    await expect(createQrAssets(destination)).resolves.toEqual({
      pngDataUrl: 'data:image/png;base64,qr',
      svg: '<svg aria-label="qr" />',
    });
    expect(qrMocks.toDataURL).toHaveBeenCalledWith(destination, expect.objectContaining({ errorCorrectionLevel: 'M' }));
    expect(qrMocks.toString).toHaveBeenCalledWith(destination, expect.objectContaining({ type: 'svg' }));
  });

  it('rejects an empty destination and creates safe filenames', async () => {
    await expect(createQrAssets('   ')).rejects.toThrow('destination URL');
    expect(safeDownloadName(' Sales Concierge / India ')).toBe('sales-concierge-india');
  });
});
