import { defineConfig } from 'astro/config';
import cloudflare from '@astrojs/cloudflare';
import react from '@astrojs/react';
import sitemap from '@astrojs/sitemap';
import tailwind from '@astrojs/tailwind';

export default defineConfig({
  // Static output — all pages pre-rendered at build time.
  // DB is queried during `astro build` in Node.js via @libsql/client.
  output: 'static',
  adapter: cloudflare(),

  integrations: [react(), sitemap(), tailwind()],

  site: 'https://theconsumercompass.com',

  vite: {
    ssr: {
      external: ['@libsql/client'],
    },
  },
});
