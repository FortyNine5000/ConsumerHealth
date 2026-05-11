export const BAND_CONFIG = [
  { min: 85, label: 'Very Strong', color: '#1a7c3e', tailwind: 'band-very-strong' },
  { min: 70, label: 'Healthy',     color: '#2ecc71', tailwind: 'band-healthy' },
  { min: 55, label: 'Mixed',       color: '#f0c419', tailwind: 'band-mixed' },
  { min: 40, label: 'Weakening',   color: '#e67e22', tailwind: 'band-weakening' },
  { min: 25, label: 'Stressed',    color: '#e74c3c', tailwind: 'band-stressed' },
  { min: 0,  label: 'Crisis',      color: '#8b0000', tailwind: 'band-crisis' },
] as const;

export function scoreToBand(score: number): typeof BAND_CONFIG[number] {
  for (const band of BAND_CONFIG) {
    if (score >= band.min) return band;
  }
  return BAND_CONFIG[BAND_CONFIG.length - 1];
}

export function formatScore(score: number | null): string {
  if (score === null) return '—';
  return score.toFixed(1);
}

export function formatDelta(delta: number | null): string {
  if (delta === null) return '—';
  const sign = delta >= 0 ? '+' : '';
  return `${sign}${delta.toFixed(1)}`;
}

export function deltaColor(delta: number | null): string {
  if (delta === null) return 'text-compass-slate';
  if (delta > 0) return 'text-green-600';
  if (delta < 0) return 'text-red-600';
  return 'text-compass-slate';
}

export const SUBSCORE_LABELS: Record<string, string> = {
  labor_income: 'Labor & Income',
  household_balance_sheet: 'Balance Sheet',
  credit_stress: 'Credit Stress',
  spending_demand: 'Spending & Demand',
  sentiment_expectations: 'Sentiment',
  inflation_affordability: 'Inflation',
  big_ticket_affordability: 'Big Ticket',
};

export const SUBSCORE_DESCRIPTIONS: Record<string, string> = {
  labor_income: 'Employment conditions, payroll growth, claims, and real wage gains.',
  household_balance_sheet: 'Savings rate, real income growth, debt service burden, and net worth.',
  credit_stress: 'Delinquency rates, charge-offs, serious delinquency transitions, and lending standards.',
  spending_demand: 'Real PCE momentum, retail sales, travel demand, and food services.',
  sentiment_expectations: 'Consumer confidence, inflation expectations, and financial distress surveys.',
  inflation_affordability: 'CPI proximity to 2% target, shelter costs, and gasoline prices.',
  big_ticket_affordability: 'Mortgage rates, auto loan rates, credit card rates, and housing affordability.',
};

export function formatDate(dateStr: string): string {
  const d = new Date(dateStr + 'T00:00:00Z');
  return d.toLocaleDateString('en-US', { year: 'numeric', month: 'short', timeZone: 'UTC' });
}

export function formatQuarter(dateStr: string): string {
  const d = new Date(dateStr + 'T00:00:00Z');
  const month = d.getUTCMonth();
  const year = d.getUTCFullYear();
  const q = Math.floor(month / 3) + 1;
  return `Q${q} ${year}`;
}
