import axios from 'axios'

function makeApi(tokenKey: string, loginPath: string) {
  const instance = axios.create({
    baseURL: '/api',
    headers: { 'Content-Type': 'application/json' },
  })

  instance.interceptors.request.use((config) => {
    const token = localStorage.getItem(tokenKey)
    if (token) {
      config.headers.Authorization = `Bearer ${token}`
    }
    return config
  })

  instance.interceptors.response.use(
    (r) => r,
    (err) => {
      if (err.response?.status === 401) {
        localStorage.removeItem(tokenKey)
        if (location.pathname !== loginPath && !location.pathname.startsWith('/signup')) {
          location.href = loginPath
        }
      }
      return Promise.reject(err)
    }
  )

  return instance
}

export const adminApi = makeApi('admin_token', '/admin/login')
export const clientApi = makeApi('client_token', '/login')

export const api = adminApi

export type Config = {
  id: number
  keyword: string
  country: string
  vertical: string
  is_active: boolean
  notes: string | null
  partner: string | null
  category: string | null
  created_at: string
  updated_at: string
  ads_count: number
  last_parsed_at: string | null
}

export type Creative = {
  id: number
  media_type: string
  s3_url: string | null
  original_url: string
  width: number | null
  height: number | null
}

export type Ad = {
  id: number
  library_id: string
  country: string
  keyword: string | null
  vertical: string | null
  page_id: string | null
  title: string | null
  page_name: string | null
  caption: string | null
  page_url: string | null
  platforms: string[] | null
  lead_form: boolean
  language: string | null
  app_store: string | null
  ecom_platform: string | null
  ip: string | null
  body: string | null
  cta_text: string | null
  link_url: string | null
  display_url: string | null
  media_type: string
  started_at: string | null
  is_active: boolean
  days_active: number
  first_seen_at: string
  last_seen_at: string
  duplicates_count: number
  creatives: Creative[]
}

export type ModerationItem = {
  id: number
  status: string
  created_at: string
  reviewed_at: string | null
  reject_reason: string | null
  ad: Ad
}

export type ClientUser = {
  id: number
  email: string
  email_verified: boolean
  referral_source: string | null
  created_at: string
  last_login_at: string | null
}