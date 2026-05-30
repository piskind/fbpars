import { create } from 'zustand'
import axios from 'axios'
import { adminApi } from '../api/client'

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
  token: localStorage.getItem('admin_token'),
  login: null,
  loading: false,
  error: null,

  signIn: async (login, password) => {
    set({ loading: true, error: null })
    try {
      const { data } = await adminApi.post('/auth/login', { login, password })
      localStorage.setItem('admin_token', data.access_token)
      set({ token: data.access_token, loading: false })
      return true
    } catch (e: unknown) {
      const msg = axios.isAxiosError(e)
        ? (e.response?.data?.detail ?? e.message)
        : 'Ошибка авторизации'
      set({ loading: false, error: msg })
      return false
    }
  },

  signOut: () => {
    localStorage.removeItem('admin_token')
    set({ token: null, login: null })
  },

  loadMe: async () => {
    try {
      const { data } = await adminApi.get('/auth/me')
      set({ login: data.login })
    } catch {
      set({ token: null, login: null })
      localStorage.removeItem('admin_token')
    }
  },
}))