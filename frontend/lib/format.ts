export function formatMoney(value: number | undefined | null, currency?: string, locale: string = 'ko-KR') {
  if (value === undefined || value === null || Number.isNaN(Number(value))) return '-';
  const ccy = (currency || 'USD').toUpperCase();
  try {
    return new Intl.NumberFormat(locale, {
      style: 'currency',
      currency: ccy,
      maximumFractionDigits: ccy === 'KRW' ? 0 : 2,
    }).format(Number(value));
  } catch {
    return Number(value).toLocaleString(locale);
  }
}
