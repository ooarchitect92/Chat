import { fileURLToPath, URL } from 'node:url';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  define: {
    'import.meta.env.VITE_API_URL': JSON.stringify('/api/v1'),
    'import.meta.env.VITE_DEMO_MODE': JSON.stringify('false'),
  },
  resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    css: true,
    coverage: { reporter: ['text', 'html'] },
  },
});
