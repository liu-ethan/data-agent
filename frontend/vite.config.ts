import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig, type Plugin } from 'vite'
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
  const memory = backend.memory ?? {}
  return {
    host: String(frontend.host ?? '0.0.0.0'),
    port: Number(frontend.port ?? 5173),
    // 缺省空串：走 Vite /api 代理；显式配置时可直连后端
    apiBaseUrl: String(frontend.api_base_url ?? ''),
    backendPort: Number(backend.port ?? 8000),
    tablesPageSize: Number(
      frontend.tables_page_size ?? backend.tables_page_size ?? 50,
    ),
    sessionTitleMaxChars: Number(memory.session_title_max_chars ?? 10),
  }
}

const appConfig = loadAppConfig()

/** 可靠注入根目录 config.yaml 的前端段（Vite define 对本项目标识符未生效） */
function appConfigPlugin(): Plugin {
  const virtualId = 'virtual:app-config'
  const resolvedId = `\0${virtualId}`
  const payload = {
    apiBaseUrl: appConfig.apiBaseUrl,
    backendPort: appConfig.backendPort,
    tablesPageSize: appConfig.tablesPageSize,
    sessionTitleMaxChars: appConfig.sessionTitleMaxChars,
  }
  return {
    name: 'app-config',
    resolveId(id) {
      if (id === virtualId) return resolvedId
    },
    load(id) {
      if (id === resolvedId) {
        return `export const appConfig = ${JSON.stringify(payload)}`
      }
    },
  }
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [appConfigPlugin(), react()],
  server: {
    port: appConfig.port,
    host: appConfig.host,
    // 同源 /api → 后端，浏览器不必直连 :8000（避免局域网/防火墙 Failed to fetch）
    proxy: {
      '/api': {
        target: `http://127.0.0.1:${appConfig.backendPort}`,
        changeOrigin: true,
      },
    },
  },
})
