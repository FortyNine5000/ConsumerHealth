/** @type {import('tailwindcss').Config} */
export default {
  content: ['./src/**/*.{astro,html,js,jsx,ts,tsx,svelte,md,mdx}'],
  theme: {
    extend: {
      colors: {
        // Score band colors
        'band-very-strong': '#1a7c3e',
        'band-healthy':     '#2ecc71',
        'band-mixed':       '#f0c419',
        'band-weakening':   '#e67e22',
        'band-stressed':    '#e74c3c',
        'band-crisis':      '#8b0000',
        // Brand
        'compass-navy':     '#1a2744',
        'compass-teal':     '#0d9488',
        'compass-slate':    '#475569',
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'Consolas', 'monospace'],
      },
    },
  },
  plugins: [],
};
