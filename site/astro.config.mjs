// @ts-check
import node from '@astrojs/node';
import { defineConfig } from 'astro/config';

// SSR everywhere by default; the handful of static pages opt in with
// `export const prerender = true`. Cloud Run provides PORT; the node
// adapter's standalone server reads HOST/PORT env at runtime.
export default defineConfig({
  output: 'server',
  adapter: node({ mode: 'standalone' }),
  site: process.env.SITE_URL || 'https://recalllookup.example',
  trailingSlash: 'ignore',
  server: { host: true },
});
