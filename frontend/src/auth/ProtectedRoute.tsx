import type { ReactNode } from 'react'
import { RequireAuth } from './RequireAuth'

/** Alias for RequireAuth — unauthenticated users are redirected to `/`. */
export function ProtectedRoute({ children }: { children: ReactNode }) {
  return <RequireAuth>{children}</RequireAuth>
}
