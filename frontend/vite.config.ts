import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import YAML from 'yaml'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const repoRoot = path.resolve(__dirname, '..')
const configPath = path.join(repoRoot, 'config.yaml')
const templatePath = path.join(repoRoot, 'config_template.yaml')

function loadAppConfig() {
  const file = fs.existsSync(configPath) ? configPath : templatePath
  const raw = YAML.parse(fs.readFileSync(file, 'utf8')) ?? {}
  const frontend = raw.frontend ?? {}
  const backend = raw.backend ?? {}
  return {
    port: Number(frontend.port ?? 5173),
    apiBaseUrl: String(frontend.api_base_url ?? 'http://127.0.0.1:8000'),
    backendPort: Number(backend.port ?? 8000),
  }
}

const appConfig = loadAppConfig()

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: appConfig.port,
    host: '127.0.0.1',
  },
  define: {
    __APP_CONFIG__: JSON.stringify({
      apiBaseUrl: appConfig.apiBaseUrl,
      backendPort: appConfig.backendPort,
    }),
  },
})
