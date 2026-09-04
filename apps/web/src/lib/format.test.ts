import { describe, expect, it, vi } from 'vitest';
import { formatCompact, formatPercent, relativeTime } from '@/lib/format';

describe('format helpers', () => {
  it('formats dashboard metrics consistently', () => {
    expect(formatCompact(1_284)).toBe('1.3K');
    expect(formatPercent(86.7)).toBe('86.7%');
    expect(formatPercent(87)).toBe('87%');
  });

  it('uses readable relative time buckets', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-09-04T12:00:00Z'));
    expect(relativeTime('2026-09-04T11:56:00Z')).toBe('4m ago');
    expect(relativeTime('2026-09-04T08:00:00Z')).toBe('4h ago');
    vi.useRealTimers();
  });
});
