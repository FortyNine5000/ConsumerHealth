import { defineConfig } from 'astro/config';
import cloudflare from '@astrojs/cloudflare';
import react from '@astrojs/react';
import sitemap from '@astrojs/sitemap';
import tailwindcss from '@tailwindcss/vite';

export default defineConfig({
  // Static output — all pages pre-rendered at build time.
  // DB is queried during `astro build` in Node.js via @libsql/client.
  output: 'static',
  adapter: cloudflare(),

  integrations: [react(), sitemap()],

  site: 'https://theconsumercompass.com',

  vite: {
    plugins: [tailwindcss()],
    ssr: {
      external: ['@libsql/client'],
    },
  },
});
