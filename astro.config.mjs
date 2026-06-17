import { defineConfig } from 'astro/config';

export default defineConfig({
  site: 'https://danielcheung.github.io',
  base: '/SimpleStockWorker/',
  output: 'static',
  build: {
    assets: 'assets'
  }
});
