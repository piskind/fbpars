import type { ReactNode } from 'react'
import { useEffect } from 'react'
import { Navigate } from 'react-router-dom'
import { useClientAuth } from '../../clientAuth/store'

export function ClientPrivateRoute({ children }: { children: ReactNode }) {
  const { token, user, loadMe } = useClientAuth()

  useEffect(() => {
    if (token && !user) {
      loadMe()
    }
  }, [token, user, loadMe])

  if (!token) {
    return <Navigate to="/login" replace />
  }

  return <>{children}</>
}