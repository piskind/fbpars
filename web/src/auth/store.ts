import { create } from 'zustand'
import { api } from '../api/client'

type State = {
  token: string | null
  login: string | null
  loading: boolean
  error: string | null
  signIn: (login: string, password: string) => Promise<boolean>
  signOut: () => void
  loadMe: () => Promise<void>
}

export const useAuth = create<State>((set) => ({
  token: localStorage.getItem('token'),
  login: null,
  loading: false,
  error: null,

  signIn: async (login, password) => {
    set({ loading: true, error: null })
    try {
      const { data } = await api.post('/auth/login', { login, password })
      localStorage.setItem('token', data.access_token)
      set({ token: data.access_token, loading: false })
      return true
    } catch (e: any) {
      set({
        loading: false,
        error: e?.response?.data?.detail || 'Ошибка авторизации',
      })
      return false
    }
  },

  signOut: () => {
    localStorage.removeItem('token')
    set({ token: null, login: null })
  },

  loadMe: async () => {
    try {
      const { data } = await api.get('/auth/me')
      set({ login: data.login })
    } catch {
      set({ token: null, login: null })
      localStorage.removeItem('token')
    }
  },
}))