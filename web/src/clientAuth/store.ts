import { create } from 'zustand'
import { clientApi } from '../api/client'
import type { ClientUser } from '../api/client'

type State = {
  token: string | null
  user: ClientUser | null
  loading: boolean
  error: string | null
  signUp: (email: string, password: string) => Promise<{ ok: boolean; verifyUrl?: string }>
  signIn: (email: string, password: string) => Promise<boolean>
  signOut: () => void
  loadMe: () => Promise<void>
}

export const useClientAuth = create<State>((set) => ({
  token: localStorage.getItem('client_token'),
  user: null,
  loading: false,
  error: null,
  signUp: async (email, password) => {
    set({ loading: true, error: null })
    try {
      const { data } = await clientApi.post('/client/signup', { email, password })
      set({ loading: false })
      return { ok: true, verifyUrl: data.verification_url }
    } catch (e: any) {
      set({
        loading: false,
        error: e?.response?.data?.detail || 'Ошибка регистрации',
      })
      return { ok: false }
    }
  },
  signIn: async (email, password) => {
    set({ loading: true, error: null })
    try {
      const { data } = await clientApi.post('/client/login', { email, password })
      localStorage.setItem('client_token', data.access_token)
      set({ token: data.access_token, loading: false })
      return true
    } catch (e: any) {
      set({
        loading: false,
        error: e?.response?.data?.detail || 'Неверный email или пароль',
      })
      return false
    }
  },
  signOut: () => {
    localStorage.removeItem('client_token')
    set({ token: null, user: null })
  },
  loadMe: async () => {
    try {
      const { data } = await clientApi.get('/client/me')
      set({ user: data })
    } catch {
      set({ token: null, user: null })
      localStorage.removeItem('client_token')
    }
  },
}))