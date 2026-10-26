// Stage current Python before starting Vite; Vite does not watch Python itself.
import { createServer } from 'vite';
await import('./prepare.mjs');
const port = Number(process.env.PORT || process.env.PW_PORT || 5186);
if (!Number.isInteger(port) || port < 1024 || port > 65535) throw new Error('Invalid PORT');
const server = await createServer({ server: { host: '127.0.0.1', port, strictPort: true } });
await server.listen();
server.printUrls();
