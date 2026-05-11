// Shared ECharts theme config for Consumer Compass charts

export const COMPASS_THEME = {
  backgroundColor: 'transparent',
  textStyle: { fontFamily: 'Inter, system-ui, sans-serif', color: '#475569' },
  color: ['#0d9488', '#1a2744', '#f0c419', '#e74c3c', '#2ecc71', '#e67e22'],
};

// NBER recession date ranges (start, end) for chart overlays
export const NBER_RECESSIONS = [
  ['1990-07-01', '1991-03-01'],
  ['2001-03-01', '2001-11-01'],
  ['2007-12-01', '2009-06-01'],
  ['2020-02-01', '2020-04-01'],
];

export function buildRecessionMarkAreas() {
  return NBER_RECESSIONS.map(([start, end]) => [
    { xAxis: start, itemStyle: { color: 'rgba(100,100,100,0.12)' } },
    { xAxis: end },
  ]);
}

export function bandColorForScore(score: number): string {
  if (score >= 85) return '#1a7c3e';
  if (score >= 70) return '#2ecc71';
  if (score >= 55) return '#f0c419';
  if (score >= 40) return '#e67e22';
  if (score >= 25) return '#e74c3c';
  return '#8b0000';
}

export interface SparklineOptions {
  data: number[];
  color?: string;
  height?: number;
  width?: number;
}

export function buildSparklineOption({ data, color = '#0d9488', height = 40, width = 120 }: SparklineOptions) {
  return {
    animation: false,
    grid: { top: 2, bottom: 2, left: 2, right: 2 },
    xAxis: { type: 'category', show: false, data: data.map((_, i) => i) },
    yAxis: { type: 'value', show: false, min: 0, max: 100 },
    series: [{
      type: 'line',
      data,
      smooth: true,
      symbol: 'none',
      lineStyle: { color, width: 2 },
      areaStyle: { color, opacity: 0.1 },
    }],
    width,
    height,
  };
}
