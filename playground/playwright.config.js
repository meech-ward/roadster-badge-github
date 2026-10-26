import { defineConfig } from '@playwright/test';

// All Roadster tests require real native pico3d; never silently skip that profile.
process.env.BADGE_RUNTIME = 'pico3d';
const port = Number(process.env.PW_PORT || 5186);
const baseURL = `http://127.0.0.1:${port}`;
export default defineConfig({
  testDir: './e2e',
  timeout: 60000,
  workers: 1,
  reporter: [['list'], ['json', { outputFile: 'test-results/results.json' }]],
  use: {
    baseURL,
    viewport: { width: 1280, height: 900 },
    launchOptions: process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {},
  },
  webServer: {
    command: 'npm run dev',
    url: baseURL,
    env: { PORT: String(port), BADGE_RUNTIME: 'pico3d' },
    // A new server restages current Python. Reuse must be explicitly requested.
    reuseExistingServer: process.env.PW_REUSE === '1',
    timeout: 60000,
  },
});
