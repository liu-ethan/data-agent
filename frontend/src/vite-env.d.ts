/// <reference types="vite/client" />

declare module 'virtual:app-config' {
  export const appConfig: {
    apiBaseUrl: string
    backendPort: number
  }
}
