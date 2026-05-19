import { Navigate } from 'react-router-dom'
import { ReactNode, useEffect } from 'react'
import { useAuth } from '../auth/store'

export function PrivateRoute({ children }: { children: ReactNode }) {
  const { token, loadMe } = useAuth()

  useEffect(() => {
    if (token) loadMe()
  }, [token])

  if (!token) return <Navigate to="/login" replace />
  return <>{children}</>
}