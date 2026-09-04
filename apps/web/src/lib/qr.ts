import QRCode from 'qrcode';

export interface QrAssets {
  pngDataUrl: string;
  svg: string;
}

const qrOptions = {
  color: { dark: '#0b1626', light: '#ffffff' },
  errorCorrectionLevel: 'M' as const,
  margin: 4,
  width: 512,
};

export async function createQrAssets(value: string): Promise<QrAssets> {
  if (!value.trim()) throw new Error('A destination URL is required.');

  const [pngDataUrl, svg] = await Promise.all([
    QRCode.toDataURL(value, qrOptions),
    QRCode.toString(value, { ...qrOptions, type: 'svg' }),
  ]);

  return { pngDataUrl, svg };
}

export function safeDownloadName(value: string): string {
  const normalized = value.trim().toLowerCase().replace(/[^a-z0-9_-]+/g, '-').replace(/^-+|-+$/g, '');
  return normalized || 'widget';
}

export function downloadQrAsset(assets: QrAssets, format: 'png' | 'svg', name: string): void {
  const anchor = document.createElement('a');
  const baseName = `${safeDownloadName(name)}-qr`;
  let objectUrl: string | undefined;

  if (format === 'png') {
    anchor.href = assets.pngDataUrl;
  } else {
    objectUrl = URL.createObjectURL(new Blob([assets.svg], { type: 'image/svg+xml;charset=utf-8' }));
    anchor.href = objectUrl;
  }

  anchor.download = `${baseName}.${format}`;
  anchor.rel = 'noopener';
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  if (objectUrl) window.setTimeout(() => URL.revokeObjectURL(objectUrl), 0);
}
