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

// Осмысленное имя файла для скачиваемого креатива: library_id + расширение.
export function mediaFilename(
  libraryId: string | null,
  mediaType: string | null,
  url: string,
): string {
  const base = libraryId || 'creative'
  let ext = (mediaType || '').toLowerCase() === 'video' ? 'mp4' : 'jpg'
  const m = url.split('?')[0].match(/\.([a-z0-9]{2,4})$/i)
  if (m) ext = m[1].toLowerCase()
  return `${base}.${ext}`
}

// Принудительное скачивание файла (не открытие вкладки): тянем blob и сохраняем.
// Нужно для видео/картинок на presigned S3, где download-атрибут игнорируется.
export async function downloadMedia(url: string, filename: string): Promise<void> {
  try {
    const res = await fetch(url)
    if (!res.ok) throw new Error(String(res.status))
    const blob = await res.blob()
    const objUrl = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = objUrl
    a.download = filename
    document.body.appendChild(a)
    a.click()
    a.remove()
    setTimeout(() => URL.revokeObjectURL(objUrl), 1000)
  } catch {
    window.open(url, '_blank', 'noopener,noreferrer')
  }
}

export type Config = {
  id: number
  config_type: 'keyword' | 'filters' | 'fanpage'
  keyword: string | null
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
  active_status: string | null
  media_type_filter: string | null
  platforms: string[] | null
  date_from: string | null
  date_to: string | null
  advertiser: string | null
  auto_date_from_last_parse: boolean
  languages: string[] | null
  sort_mode: string
  sort_direction: string
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
  partner: string | null
  duplicates_count: number
  reach: number | null
  reach_breakdown: {
    targeting?: {
      age?: string
      gender?: string
      countries_included?: string[]
    }
    demographic?: Array<{
      location: string
      age: string
      gender: string
      reach: number
    }>
  } | null
  eu_countries: string[] | null
  spend_estimate: number | null
  used_in_ads_count: number | null
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