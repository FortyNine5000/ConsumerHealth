import { createClient } from '@libsql/client';

function getClient() {
  return createClient({
    url: import.meta.env.TURSO_DATABASE_URL,
    authToken: import.meta.env.TURSO_AUTH_TOKEN,
  });
}

export interface HeadlineScore {
  score_date: string;
  score: number;
  band: string;
  band_color: string;
  delta_1m: number | null;
  delta_3m: number | null;
  delta_12m: number | null;
  biggest_gains: string | null;
  biggest_drops: string | null;
  one_liner: string | null;
}

export interface SubScore {
  slug: string;
  score_date: string;
  score: number;
  label: string;
  weight: number;
  sparkline: number[];
}

export interface IndicatorRow {
  slug: string;
  label: string;
  subscore: string;
  series_id: string;
  frequency: string;
  scoring_type: string;
  higher_is_better: number | null;
  weight_in_subscore: number;
  is_scored: number;
  latest_score: number | null;
  latest_value: number | null;
  latest_date: string | null;
  source_name: string | null;
  source_url: string | null;
  description: string | null;
  unit: string | null;
  sparkline: number[];
  score_sparkline: number[];
}

export interface IndicatorHistory {
  score_date: string;
  raw_value: number | null;
  score: number | null;
  smoothed_score: number | null;
  percentile_rank: number | null;
}

export interface QuoteRow {
  id: number;
  ticker: string;
  company_name: string;
  sector: string;
  quarter: string;
  quote_text: string;
  category: string[];
  sentiment_score: number;
  agrees_with_dashboard: number | null;
  filed_at: string;
}

const SUBSCORE_LABELS: Record<string, string> = {
  labor_income: 'Labor & Income',
  household_balance_sheet: 'Balance Sheet',
  credit_stress: 'Credit Stress',
  spending_demand: 'Spending & Demand',
  sentiment_expectations: 'Sentiment',
  inflation_affordability: 'Inflation',
  big_ticket_affordability: 'Big Ticket',
};

const SUBSCORE_WEIGHTS: Record<string, number> = {
  labor_income: 0.20,
  household_balance_sheet: 0.15,
  credit_stress: 0.20,
  spending_demand: 0.15,
  sentiment_expectations: 0.10,
  inflation_affordability: 0.10,
  big_ticket_affordability: 0.10,
};

export async function getLatestHeadlineScore(): Promise<HeadlineScore | null> {
  const client = getClient();
  try {
    const result = await client.execute(
      `SELECT score_date, score, band, band_color, delta_1m, delta_3m, delta_12m,
              biggest_gains, biggest_drops
       FROM headline_scores
       ORDER BY score_date DESC
       LIMIT 1`
    );
    if (result.rows.length === 0) return null;
    const r = result.rows[0];
    return {
      score_date: r[0] as string,
      score: r[1] as number,
      band: r[2] as string,
      band_color: r[3] as string,
      delta_1m: r[4] as number | null,
      delta_3m: r[5] as number | null,
      delta_12m: r[6] as number | null,
      biggest_gains: r[7] as string | null,
      biggest_drops: r[8] as string | null,
      one_liner: null,
    };
  } finally {
    client.close();
  }
}

export async function getLatestSubScores(): Promise<SubScore[]> {
  const client = getClient();
  try {
    const latest = await client.execute(
      `SELECT MAX(score_date) FROM subscores`
    );
    const latestDate = latest.rows[0]?.[0] as string | null;
    if (!latestDate) return [];

    const result = await client.execute({
      sql: `SELECT slug, score_date, score FROM subscores WHERE score_date = ?`,
      args: [latestDate],
    });

    const sparklineResults: number[][] = [];
    for (const row of result.rows) {
      const slug = row[0] as string;
      const spark = await client.execute({
        sql: `SELECT score FROM subscores WHERE slug = ? ORDER BY score_date DESC LIMIT 12`,
        args: [slug],
      });
      sparklineResults.push(spark.rows.map((r) => r[0] as number).reverse());
    }

    return result.rows.map((r, i) => {
      const slug = r[0] as string;
      return {
        slug,
        score_date: r[1] as string,
        score: r[2] as number,
        label: SUBSCORE_LABELS[slug] ?? slug,
        weight: SUBSCORE_WEIGHTS[slug] ?? 0,
        sparkline: sparklineResults[i],
      };
    });
  } finally {
    client.close();
  }
}

export async function getAllIndicators(): Promise<IndicatorRow[]> {
  const client = getClient();
  try {
    const result = await client.execute(
      `SELECT i.id, i.slug, i.name, i.subscore, i.series_id, i.frequency,
              i.scoring_type, i.higher_is_better, i.weight_in_subscore, i.is_scored,
              s.name AS source_name, s.base_url AS source_url,
              i.description_md, i.units,
              (SELECT sc.smoothed_score FROM indicator_scores sc WHERE sc.indicator_id = i.id ORDER BY sc.score_date DESC LIMIT 1) AS latest_score,
              (SELECT sc.raw_value FROM indicator_scores sc WHERE sc.indicator_id = i.id ORDER BY sc.score_date DESC LIMIT 1) AS latest_value,
              (SELECT sc.score_date FROM indicator_scores sc WHERE sc.indicator_id = i.id ORDER BY sc.score_date DESC LIMIT 1) AS latest_date
       FROM indicators i
       LEFT JOIN sources s ON s.id = i.source_id
       ORDER BY i.subscore, i.weight_in_subscore DESC`
    );

    const rows = result.rows.map((r) => ({
      id: r[0] as number,
      indicator: {
        slug: r[1] as string,
        label: r[2] as string,
        subscore: r[3] as string,
        series_id: r[4] as string,
        frequency: r[5] as string,
        scoring_type: r[6] as string,
        higher_is_better: r[7] as number | null,
        weight_in_subscore: r[8] as number,
        is_scored: r[9] as number,
        source_name: r[10] as string | null,
        source_url: r[11] as string | null,
        description: r[12] as string | null,
        unit: r[13] as string | null,
        latest_score: r[14] as number | null,
        latest_value: r[15] as number | null,
        latest_date: r[16] as string | null,
        sparkline: [] as number[],
        score_sparkline: [] as number[],
      },
    }));

    for (const row of rows) {
      const spark = await client.execute({
        sql: `SELECT raw_value, smoothed_score, score
              FROM indicator_scores
              WHERE indicator_id = ?
              ORDER BY score_date DESC
              LIMIT 48`,
        args: [row.id],
      });

      row.indicator.sparkline = spark.rows
        .map((r) => r[0] as number | null)
        .filter((v): v is number => v != null)
        .reverse();
      row.indicator.score_sparkline = spark.rows
        .map((r) => (r[1] ?? r[2]) as number | null)
        .filter((v): v is number => v != null)
        .reverse();
    }

    return rows.map((r) => r.indicator);
  } finally {
    client.close();
  }
}

export async function getIndicatorBySlug(slug: string): Promise<IndicatorRow | null> {
  const client = getClient();
  try {
    const result = await client.execute({
      sql: `SELECT i.slug, i.name, i.subscore, i.series_id, i.frequency,
                   i.scoring_type, i.higher_is_better, i.weight_in_subscore, i.is_scored,
                   s.name AS source_name, s.base_url AS source_url,
                   i.description_md, i.units,
                   NULL AS latest_score, NULL AS latest_value, NULL AS latest_date
            FROM indicators i
            LEFT JOIN sources s ON s.id = i.source_id
            WHERE i.slug = ?`,
      args: [slug],
    });
    if (result.rows.length === 0) return null;
    const r = result.rows[0];
    return {
      slug: r[0] as string,
      label: r[1] as string,
      subscore: r[2] as string,
      series_id: r[3] as string,
      frequency: r[4] as string,
      scoring_type: r[5] as string,
      higher_is_better: r[6] as number | null,
      weight_in_subscore: r[7] as number,
      is_scored: r[8] as number,
      source_name: r[9] as string | null,
      source_url: r[10] as string | null,
      description: r[11] as string | null,
      unit: r[12] as string | null,
      latest_score: null,
      latest_value: null,
      latest_date: null,
      sparkline: [],
      score_sparkline: [],
    };
  } finally {
    client.close();
  }
}

export async function getIndicatorHistory(slug: string): Promise<IndicatorHistory[]> {
  const client = getClient();
  try {
    const result = await client.execute({
      sql: `SELECT sc.score_date, sc.raw_value, sc.score, sc.smoothed_score, sc.percentile_rank
            FROM indicator_scores sc
            JOIN indicators i ON i.id = sc.indicator_id
            WHERE i.slug = ?
            ORDER BY sc.score_date ASC`,
      args: [slug],
    });
    return result.rows.map((r) => ({
      score_date: r[0] as string,
      raw_value: r[1] as number | null,
      score: r[2] as number | null,
      smoothed_score: r[3] as number | null,
      percentile_rank: r[4] as number | null,
    }));
  } finally {
    client.close();
  }
}

export async function getSubScoreHistory(slug: string): Promise<{ score_date: string; score: number }[]> {
  const client = getClient();
  try {
    const result = await client.execute({
      sql: `SELECT score_date, score FROM subscores WHERE slug = ? ORDER BY score_date ASC`,
      args: [slug],
    });
    return result.rows.map((r) => ({
      score_date: r[0] as string,
      score: r[1] as number,
    }));
  } finally {
    client.close();
  }
}

export async function getHeadlineHistory(): Promise<{ score_date: string; score: number; band: string }[]> {
  const client = getClient();
  try {
    const result = await client.execute(
      `SELECT score_date, score, band FROM headline_scores ORDER BY score_date ASC`
    );
    return result.rows.map((r) => ({
      score_date: r[0] as string,
      score: r[1] as number,
      band: r[2] as string,
    }));
  } finally {
    client.close();
  }
}

export async function getRecentQuotes(limit = 20): Promise<QuoteRow[]> {
  const client = getClient();
  try {
    const result = await client.execute({
      sql: `SELECT eq.id, c.ticker, c.name AS company_name, c.sector, eq.quarter,
                   eq.quote_text, eq.category, eq.sentiment_score, eq.agrees_with_dashboard, eq.filed_at
            FROM earnings_quotes eq
            JOIN companies c ON c.id = eq.company_id
            ORDER BY eq.filed_at DESC
            LIMIT ?`,
      args: [limit],
    });
    return result.rows.map((r) => ({
      id: r[0] as number,
      ticker: r[1] as string,
      company_name: r[2] as string,
      sector: r[3] as string,
      quarter: r[4] as string,
      quote_text: r[5] as string,
      category: JSON.parse((r[6] as string) || '[]'),
      sentiment_score: r[7] as number,
      agrees_with_dashboard: r[8] as number | null,
      filed_at: r[9] as string,
    }));
  } finally {
    client.close();
  }
}

export async function getAllQuotes(): Promise<QuoteRow[]> {
  return getRecentQuotes(500);
}
