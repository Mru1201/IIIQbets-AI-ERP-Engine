import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

export default defineConfig({
  plugins: [react()],
  define: {
    'process.env.NODE_ENV': JSON.stringify('production'),
  },
  build: {
    lib: {
      entry: path.resolve(__dirname, 'src/webcomponent/ErpAiReporterElement.tsx'),
      name: 'ErpAiReporter',
      fileName: (format) => `erp-ai-reporter.${format}.js`,
      formats: ['es', 'umd'],
    },
    outDir: 'dist/wc',
  },
})
