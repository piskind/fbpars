import { Navigate } from 'react-router-dom'
import { useEffect } from 'react'
import type { ReactNode } from 'react'
import { useAuth } from '../auth/store'

export function PrivateRoute({ children }: { children: ReactNode }) {
  const { token, loadMe } = useAuth()

  useEffect(() => {
    if (token) loadMe()
  }, [token])

  if (!token) return <Navigate to="/login" replace />
  return <>{children}</>
}