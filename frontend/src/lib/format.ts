export const formatCompact = (value: number): string => Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 1 }).format(value);
export const formatPercent = (value: number): string => `${value.toFixed(value % 1 ? 1 : 0)}%`;
export const formatDate = (value: string): string => new Intl.DateTimeFormat('en', { month: 'short', day: 'numeric', year: new Date(value).getFullYear() !== new Date().getFullYear() ? 'numeric' : undefined }).format(new Date(value));
export const relativeTime = (value: string): string => {
  const diff = Date.now() - new Date(value).getTime();
  const minute = 60_000; const hour = minute * 60; const day = hour * 24;
  if (diff < minute) return 'just now';
  if (diff < hour) return `${Math.floor(diff / minute)}m ago`;
  if (diff < day) return `${Math.floor(diff / hour)}h ago`;
  if (diff < day * 7) return `${Math.floor(diff / day)}d ago`;
  return formatDate(value);
};
