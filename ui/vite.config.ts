import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath, URL } from 'node:url';

/**
 * The interface is served from the same origin as the API by the desktop host, so no
 * cross-origin configuration is needed in production. In development the API runs on
 * its own port and is proxied here.
 */
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
    // Nothing is fetched at runtime: the application must work with no network.
    assetsInlineLimit: 0,
    rollupOptions: {
      output: {
        manualChunks: {
          vendor: ['react', 'react-dom', 'react-router-dom'],
          query: ['@tanstack/react-query'],
        },
      },
    },
  },
  server: {
    port: 5174,
    proxy: {
      '/api': {
        target: process.env.NICKLAND_API ?? 'http://127.0.0.1:8099',
        changeOrigin: true,
      },
    },
  },
});
