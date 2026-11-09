import { defineConfig } from 'vite';
import {readFileSync} from 'node:fs';
const version=JSON.parse(readFileSync(new URL('./package.json',import.meta.url),'utf8')).version;
export default defineConfig({ root: 'frontend', define:{__APP_VERSION__:JSON.stringify(version)}, build: { outDir: 'dist', emptyOutDir: true }, server: { host: '127.0.0.1', proxy: { '/api': 'http://127.0.0.1:8787', '/healthz': 'http://127.0.0.1:8787' } } });
