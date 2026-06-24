import React, { useState, useEffect, useMemo, useRef } from 'react'
import { useQuery, useInfiniteQuery } from '@tanstack/react-query'
import { clientApi } from '../../api/client'
import type { Ad } from '../../api/client'
import { AdDrawer } from '../../components/client/AdDrawer'
import { countryFlag } from '../../flags'
import { Settings, SlidersHorizontal, ChevronDown, Search } from 'lucide-react'

const PAGE_SIZE = 40

function mediaUrl(s3Url: string | null): string | null {
  if (!s3Url) return null
  const m = s3Url.match(/\/((?:m|ads)\/.+)$/)
  if (!m) return null
  return `/api/media/${m[1]}`
}

function adsLibraryUrl(pageId: string | null): string | null {
  if (!pageId) return null
  return `https://www.facebook.com/ads/library/?active_status=all&ad_type=all&country=ALL&view_all_page_id=${pageId}`
}

// ─── Types ────────────────────────────────────────────────────────────────────

type Facets = {
  countries: string[]
  keywords: string[]
  verticals: string[]
  media_types: string[]
  ctas: string[]
  platforms: string[]
  languages: string[]
  app_stores: string[]
  ecom_platforms: string[]
}

type Filters = {
  countries: string[]
  search: string
  startedFrom: string
  daysMin: string
  daysMax: string
  vertical: string
  sort: string
  isActive: string
  // Ad settings card
  mediaType: string
  cta: string
  platforms: string[]
  leadForm: string
  // Fine settings card
  pageName: string
  appLink: string      // "Приложение, ID или ссылка" → link_contains
  domain: string
  linkContains: string // "В ссылке" → link_contains (fallback if appLink empty)
  appStore: string
  lastSeenFrom: string
  ecomPlatform: string
  ipQuery: string
  language: string
}

const emptyFilters: Filters = {
  countries: [],
  search: '',
  startedFrom: '',
  daysMin: '',
  daysMax: '',
  vertical: '',
  sort: 'newest',
  isActive: '',
  mediaType: '',
  cta: '',
  platforms: [],
  leadForm: '',
  pageName: '',
  appLink: '',
  domain: '',
  linkContains: '',
  appStore: '',
  lastSeenFrom: '',
  ecomPlatform: '',
  ipQuery: '',
  language: '',
}

// ─── Small components ─────────────────────────────────────────────────────────

function FieldRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="block text-xs text-gray-500 mb-1">{label}</label>
      {children}
    </div>
  )
}

function SelectField({
  value,
  onChange,
  children,
  disabled,
}: {
  value: string
  onChange?: (v: string) => void
  children: React.ReactNode
  disabled?: boolean
}) {
  return (
    <div className="relative">
      <select
        value={value}
        onChange={(e) => onChange?.(e.target.value)}
        disabled={disabled}
        className="w-full appearance-none px-3 py-2 pr-8 border rounded-lg text-sm bg-white disabled:bg-gray-100 disabled:text-gray-400 disabled:cursor-not-allowed"
      >
        {children}
      </select>
      <ChevronDown
        className={`absolute right-2.5 top-2.5 w-3.5 h-3.5 pointer-events-none ${
          disabled ? 'text-gray-300' : 'text-gray-400'
        }`}
      />
    </div>
  )
}

function InputField({
  value,
  onChange,
  placeholder,
}: {
  value: string
  onChange: (v: string) => void
  placeholder?: string
}) {
  return (
    <div className="relative">
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full px-3 py-2 pr-8 border rounded-lg text-sm"
      />
      <span className="absolute right-2.5 top-2 text-gray-300 text-sm pointer-events-none select-none">
        ƒ
      </span>
    </div>
  )
}

function CountryDropdown({
  options,
  selected,
  onChange,
}: {
  options: string[]
  selected: string[]
  onChange: (v: string[]) => void
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  const label =
    selected.length === 0
      ? 'Все'
      : selected.length <= 2
        ? selected.map((c) => `${countryFlag(c)} ${c}`).join(', ')
        : `${selected.slice(0, 2).join(', ')} +${selected.length - 2}`

  const toggle = (c: string) =>
    onChange(selected.includes(c) ? selected.filter((x) => x !== c) : [...selected, c])

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1.5 px-3 py-2 border rounded-lg text-sm bg-white hover:border-gray-400 transition min-w-[130px] justify-between"
      >
        <span className="truncate">{label}</span>
        <ChevronDown className="w-3.5 h-3.5 text-gray-400 shrink-0" />
      </button>
      {open && (
        <div className="absolute z-50 mt-1 bg-white border rounded-lg shadow-lg max-h-60 overflow-y-auto w-52">
          {options.map((c) => (
            <label
              key={c}
              className="flex items-center gap-2 px-3 py-1.5 hover:bg-gray-50 cursor-pointer text-sm"
            >
              <input
                type="checkbox"
                checked={selected.includes(c)}
                onChange={() => toggle(c)}
                className="w-3.5 h-3.5 shrink-0"
              />
              <span>
                {countryFlag(c)} {c}
              </span>
            </label>
          ))}
        </div>
      )}
    </div>
  )
}

function MultiSelectDropdown({
  options,
  selected,
  onChange,
  placeholder = 'Все',
}: {
  options: string[]
  selected: string[]
  onChange: (v: string[]) => void
  placeholder?: string
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  const label = selected.length === 0 ? placeholder : selected.join(', ')
  const toggle = (v: string) =>
    onChange(selected.includes(v) ? selected.filter((x) => x !== v) : [...selected, v])

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between px-3 py-2 border rounded-lg text-sm bg-white hover:border-gray-400"
      >
        <span className="truncate text-left">{label}</span>
        <ChevronDown className="w-3.5 h-3.5 text-gray-400 shrink-0 ml-1" />
      </button>
      {open && options.length > 0 && (
        <div className="absolute z-50 mt-1 bg-white border rounded-lg shadow-lg w-full max-h-48 overflow-y-auto">
          {options.map((o) => (
            <label
              key={o}
              className="flex items-center gap-2 px-3 py-1.5 hover:bg-gray-50 cursor-pointer text-sm"
            >
              <input
                type="checkbox"
                checked={selected.includes(o)}
                onChange={() => toggle(o)}
                className="w-3.5 h-3.5"
              />
              {o}
            </label>
          ))}
        </div>
      )}
    </div>
  )
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export function ClientFeedPage() {
  const [draft, setDraft] = useState<Filters>(emptyFilters)
  const [applied, setApplied] = useState<Filters>(emptyFilters)
  const [showSettings, setShowSettings] = useState(false)
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const sentinelRef = useRef<HTMLDivElement>(null)

  const { data: facets } = useQuery({
    queryKey: ['feed-facets'],
    queryFn: async () => (await clientApi.get<Facets>('/feed/facets')).data,
  })

  const baseParams = useMemo(() => {
    const p = new URLSearchParams()
    p.set('sort', applied.sort)
    applied.countries.forEach((c) => p.append('countries', c))
    if (applied.vertical) p.set('vertical', applied.vertical)
    if (applied.search.trim()) p.set('search', applied.search.trim())
    if (applied.startedFrom) p.set('started_from', applied.startedFrom)
    if (applied.daysMin) p.set('days_active_min', applied.daysMin)
    if (applied.daysMax) p.set('days_active_max', applied.daysMax)
    // Ad settings
    if (applied.mediaType) p.set('media_type', applied.mediaType)
    if (applied.cta) p.set('cta', applied.cta)
    applied.platforms.forEach((pl) => p.append('platforms', pl))
    if (applied.leadForm) p.set('lead_form', applied.leadForm)
    // Fine settings
    if (applied.pageName) {
      if (/^\d+$/.test(applied.pageName)) {
        p.set('page_id', applied.pageName)
      } else {
        p.set('page_name', applied.pageName)
      }
    }
    const lc = applied.appLink || applied.linkContains
    if (lc) p.set('link_contains', lc)
    if (applied.domain) p.set('domain', applied.domain)
    if (applied.appStore) p.set('app_store', applied.appStore)
    if (applied.lastSeenFrom) p.set('last_seen_from', applied.lastSeenFrom)
    if (applied.ecomPlatform) p.set('ecom_platform', applied.ecomPlatform)
    if (applied.ipQuery) p.set('ip', applied.ipQuery)
    if (applied.language) p.set('language', applied.language)
    if (applied.isActive) p.set('is_active', applied.isActive)
    return p
  }, [applied])

  const { data, isLoading, isFetchingNextPage, fetchNextPage, hasNextPage } = useInfiniteQuery({
    queryKey: ['feed', baseParams.toString()],
    queryFn: async ({ pageParam }) => {
      const p = new URLSearchParams(baseParams)
      p.set('limit', String(PAGE_SIZE))
      p.set('offset', String(pageParam))
      return (await clientApi.get<Ad[]>(`/feed?${p.toString()}`)).data
    },
    initialPageParam: 0,
    getNextPageParam: (lastPage, allPages) => {
      if (lastPage.length < PAGE_SIZE) return undefined
      return allPages.length * PAGE_SIZE
    },
  })

  const ads = useMemo(() => data?.pages.flat() ?? [], [data])

  useEffect(() => {
    const el = sentinelRef.current
    if (!el) return
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting && hasNextPage && !isFetchingNextPage) fetchNextPage()
      },
      { rootMargin: '300px' }
    )
    observer.observe(el)
    return () => observer.disconnect()
  }, [hasNextPage, isFetchingNextPage, fetchNextPage])

  useEffect(() => {
    const handler = (e: Event) => {
      const id = (e as CustomEvent).detail as number
      setSelectedId(id)
    }
    window.addEventListener('drawer:select', handler)
    return () => window.removeEventListener('drawer:select', handler)
  }, [])

  const set = (patch: Partial<Filters>) => setDraft((d) => ({ ...d, ...patch }))
  const apply = () => setApplied(draft)
  const reset = () => {
    setDraft(emptyFilters)
    setApplied(emptyFilters)
  }

  return (
    <div className="flex">
      <div className={`flex-1 transition-all ${selectedId ? 'lg:mr-[360px]' : ''}`}>
        <h1 className="text-2xl font-bold mb-4">
          Объявления{' '}
          <span className="text-gray-400 text-base font-normal">({ads.length})</span>
        </h1>

        {/* ── Filter panel ── */}
        <div className="bg-white rounded-xl shadow p-4 mb-3">

          {/* Top row: 7 fields */}
          <div className="flex flex-wrap gap-3 items-end">
            <div className="flex-1 min-w-[180px]">
              <label className="block text-xs text-gray-500 mb-1">Поиск</label>
              <div className="relative">
                <Search className="absolute left-2.5 top-2.5 w-3.5 h-3.5 text-gray-400 pointer-events-none" />
                <input
                  value={draft.search}
                  onChange={(e) => set({ search: e.target.value })}
                  onKeyDown={(e) => e.key === 'Enter' && apply()}
                  placeholder="ключевое слово"
                  className="w-full pl-8 pr-3 py-2 border rounded-lg text-sm"
                />
              </div>
            </div>

            <div>
              <label className="block text-xs text-gray-500 mb-1">Страны</label>
              <CountryDropdown
                options={facets?.countries ?? []}
                selected={draft.countries}
                onChange={(countries) => set({ countries })}
              />
            </div>

            <div>
              <label className="block text-xs text-gray-500 mb-1">Дата создания</label>
              <input
                type="date"
                value={draft.startedFrom}
                onChange={(e) => set({ startedFrom: e.target.value })}
                className="px-3 py-2 border rounded-lg text-sm"
              />
            </div>

            <div>
              <label className="block text-xs text-gray-500 mb-1">Активность дней от</label>
              <input
                type="number"
                min="0"
                value={draft.daysMin}
                onChange={(e) => set({ daysMin: e.target.value })}
                className="w-20 px-3 py-2 border rounded-lg text-sm"
              />
            </div>

            <div>
              <label className="block text-xs text-gray-500 mb-1">до</label>
              <input
                type="number"
                min="0"
                value={draft.daysMax}
                onChange={(e) => set({ daysMax: e.target.value })}
                className="w-20 px-3 py-2 border rounded-lg text-sm"
              />
            </div>

            <div>
              <label className="block text-xs text-gray-500 mb-1">Вертикаль</label>
              <div className="relative">
                <select
                  value={draft.vertical}
                  onChange={(e) => set({ vertical: e.target.value })}
                  className="appearance-none px-3 py-2 pr-8 border rounded-lg text-sm bg-white"
                >
                  <option value="">Все</option>
                  {facets?.verticals.map((v) => (
                    <option key={v} value={v}>
                      {v}
                    </option>
                  ))}
                </select>
                <ChevronDown className="absolute right-2.5 top-2.5 w-3.5 h-3.5 text-gray-400 pointer-events-none" />
              </div>
            </div>

            <div>
              <label className="block text-xs text-gray-500 mb-1">Сортировка</label>
              <div className="relative">
                <select
                  value={draft.sort}
                  onChange={(e) => set({ sort: e.target.value })}
                  className="appearance-none px-3 py-2 pr-8 border rounded-lg text-sm bg-white"
                >
                  <option value="newest">Сначала свежие</option>
                  <option value="oldest">Сначала старые</option>
                  <option value="days_desc">Дольше крутят</option>
                  <option value="days_asc">Меньше крутят</option>
                </select>
                <ChevronDown className="absolute right-2.5 top-2.5 w-3.5 h-3.5 text-gray-400 pointer-events-none" />
              </div>
            </div>

            <div>
              <label className="block text-xs text-gray-500 mb-1">Активность</label>
              <div className="relative">
                <select
                  value={draft.isActive}
                  onChange={(e) => set({ isActive: e.target.value })}
                  className="appearance-none px-3 py-2 pr-8 border rounded-lg text-sm bg-white"
                >
                  <option value="">Все</option>
                  <option value="true">Активные</option>
                  <option value="false">Неактивные</option>
                </select>
                <ChevronDown className="absolute right-2.5 top-2.5 w-3.5 h-3.5 text-gray-400 pointer-events-none" />
              </div>
            </div>
          </div>

          {/* Toggle */}
          <div className="mt-3 pt-3 border-t">
            <button
              onClick={() => setShowSettings((v) => !v)}
              className="text-sm text-blue-600 hover:underline"
            >
              {showSettings ? '▼' : '▶'} Настройки объявлений
            </button>
          </div>

          {/* Two cards */}
          {showSettings && (
            <div className="mt-3 grid grid-cols-1 lg:grid-cols-2 gap-4">

              {/* Left card */}
              <div className="bg-gray-50 border border-gray-200 rounded-xl p-5">
                <h3 className="flex items-center gap-1.5 text-xs text-gray-500 font-medium mb-4">
                  <Settings className="w-3.5 h-3.5" /> Настройки объявлений
                </h3>
                <div className="space-y-3">
                  <FieldRow label="Формат объявлений">
                    <SelectField
                      value={draft.mediaType}
                      onChange={(v) => set({ mediaType: v })}
                    >
                      <option value="">Все</option>
                      {facets?.media_types.map((m) => (
                        <option key={m} value={m}>
                          {m}
                        </option>
                      ))}
                    </SelectField>
                  </FieldRow>

                  <FieldRow label="Формат медиа">
                    <SelectField value="" disabled>
                      <option value="">Все</option>
                    </SelectField>
                  </FieldRow>

                  <FieldRow label="CTA">
                    <SelectField value={draft.cta} onChange={(v) => set({ cta: v })}>
                      <option value="">Все</option>
                      {facets?.ctas.map((c) => (
                        <option key={c} value={c}>
                          {c}
                        </option>
                      ))}
                    </SelectField>
                  </FieldRow>

                  <FieldRow label="Плейсменты">
                    <MultiSelectDropdown
                      options={facets?.platforms ?? []}
                      selected={draft.platforms}
                      onChange={(platforms) => set({ platforms })}
                    />
                  </FieldRow>

                  <FieldRow label="Lead-form">
                    <SelectField value={draft.leadForm} onChange={(v) => set({ leadForm: v })}>
                      <option value="">Все</option>
                      <option value="true">Да</option>
                      <option value="false">Нет</option>
                    </SelectField>
                  </FieldRow>
                </div>
              </div>

              {/* Right card */}
              <div className="bg-gray-50 border border-gray-200 rounded-xl p-5">
                <h3 className="flex items-center gap-1.5 text-xs text-gray-500 font-medium mb-4">
                  <SlidersHorizontal className="w-3.5 h-3.5" /> Тонкие настройки
                </h3>
                <div className="space-y-3">
                  <FieldRow label="Fan page ID или название">
                    <InputField
                      value={draft.pageName}
                      onChange={(v) => set({ pageName: v })}
                      placeholder="ID или название страницы"
                    />
                  </FieldRow>

                  <FieldRow label="Приложение, ID или ссылка">
                    <InputField
                      value={draft.appLink}
                      onChange={(v) => set({ appLink: v })}
                      placeholder="bundle ID, ссылка на приложение"
                    />
                  </FieldRow>

                  <FieldRow label="Доменная зона">
                    <InputField
                      value={draft.domain}
                      onChange={(v) => set({ domain: v })}
                      placeholder="example.com"
                    />
                  </FieldRow>

                  <FieldRow label="В ссылке">
                    <InputField
                      value={draft.linkContains}
                      onChange={(v) => set({ linkContains: v })}
                      placeholder="фрагмент URL"
                    />
                  </FieldRow>

                  <FieldRow label="Магазин приложений">
                    <SelectField value={draft.appStore} onChange={(v) => set({ appStore: v })}>
                      <option value="">Все</option>
                      {facets?.app_stores?.map((a) => (
                        <option key={a} value={a}>
                          {a}
                        </option>
                      ))}
                    </SelectField>
                  </FieldRow>

                  <FieldRow label="Последняя активность">
                    <input
                      type="date"
                      value={draft.lastSeenFrom}
                      onChange={(e) => set({ lastSeenFrom: e.target.value })}
                      className="w-full px-3 py-2 border rounded-lg text-sm"
                    />
                  </FieldRow>

                  <FieldRow label="E-com платформа">
                    <SelectField
                      value={draft.ecomPlatform}
                      onChange={(v) => set({ ecomPlatform: v })}
                    >
                      <option value="">Все</option>
                      {facets?.ecom_platforms?.map((ep) => (
                        <option key={ep} value={ep}>
                          {ep}
                        </option>
                      ))}
                    </SelectField>
                  </FieldRow>

                  <FieldRow label="IP или домен">
                    <InputField
                      value={draft.ipQuery}
                      onChange={(v) => set({ ipQuery: v })}
                      placeholder="например 31.31.196.208"
                    />
                  </FieldRow>

                  <FieldRow label="Язык объявления">
                    <SelectField value={draft.language} onChange={(v) => set({ language: v })}>
                      <option value="">Все</option>
                      {facets?.languages?.map((l) => (
                        <option key={l} value={l}>
                          {l.toUpperCase()}
                        </option>
                      ))}
                    </SelectField>
                  </FieldRow>
                </div>
              </div>
            </div>
          )}

          {/* Actions */}
          <div className="flex gap-2 pt-3 mt-3 border-t">
            <button
              onClick={apply}
              className="px-5 py-2 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700"
            >
              Найти
            </button>
            <button
              onClick={reset}
              className="px-3 py-2 text-sm text-gray-500 hover:text-gray-700"
            >
              Сбросить
            </button>
          </div>
        </div>

        {/* ── Ad grid ── */}
        {isLoading && (
          <div className="text-gray-400 py-8 text-center">Загрузка...</div>
        )}

        <div className="grid grid-cols-[repeat(auto-fill,minmax(200px,1fr))] gap-4">
          {ads.map((ad) => {
            const cre = ad.creatives.find((c) => c.s3_url) || ad.creatives[0]
            const url = cre ? mediaUrl(cre.s3_url) : null
            const isVideo = cre?.media_type?.toLowerCase() === 'video'
            const isSelected = selectedId === ad.id
            const pageFbUrl = adsLibraryUrl(ad.page_id)

            return (
              <div
                key={ad.id}
                onClick={() => setSelectedId(ad.id)}
                className={`bg-white rounded-xl shadow overflow-hidden flex flex-col cursor-pointer transition ${
                  isSelected ? 'ring-2 ring-blue-500 shadow-md' : 'hover:shadow-md'
                }`}
              >
                <div className="w-full aspect-square bg-gray-100 flex items-center justify-center overflow-hidden">
                  {url && isVideo ? (
                    <video
                      src={url}
                      className="w-full h-full object-cover"
                      muted
                      preload="metadata"
                    />
                  ) : url ? (
                    <img
                      src={url}
                      alt=""
                      className="w-full h-full object-cover"
                      loading="lazy"
                    />
                  ) : (
                    <div className="text-gray-300 text-sm">нет медиа</div>
                  )}
                </div>

                <div className="p-3 flex-1 flex flex-col">
                  <div className="flex items-center justify-between mb-1 text-sm">
                    <span className="font-medium truncate flex items-center gap-2 min-w-0">
                      {pageFbUrl ? (
                        <a
                          href={pageFbUrl}
                          target="_blank"
                          rel="noopener noreferrer"
                          onClick={(e) => e.stopPropagation()}
                          className="truncate hover:underline text-blue-600"
                        >
                          {ad.page_name || '—'}
                        </a>
                      ) : (
                        <span className="truncate">{ad.page_name || '—'}</span>
                      )}
                      {ad.duplicates_count > 0 && (
                        <span className="shrink-0 text-[10px] bg-orange-100 text-orange-700 px-1.5 py-0.5 rounded-full font-normal">
                          +{ad.duplicates_count}
                        </span>
                      )}
                    </span>
                    <span
                      className={`shrink-0 ml-2 text-xs ${
                        ad.is_active ? 'text-green-600' : 'text-gray-400'
                      }`}
                    >
                      {ad.is_active ? 'Active' : 'Inactive'}
                    </span>
                  </div>
                  <div className="text-xs text-gray-500">
                    {countryFlag(ad.country)} {ad.country} · {ad.keyword} · {ad.days_active}d
                  </div>
                </div>
              </div>
            )
          })}
        </div>

        <div ref={sentinelRef} className="h-4" />

        {isFetchingNextPage && (
          <div className="text-gray-400 py-4 text-center text-sm">Загрузка...</div>
        )}
        {!isLoading && !isFetchingNextPage && !hasNextPage && ads.length > 0 && (
          <div className="text-gray-300 py-4 text-center text-xs">Все объявления загружены</div>
        )}
        {!isLoading && ads.length === 0 && (
          <div className="text-gray-400 mt-12 text-center">Ничего не найдено</div>
        )}
      </div>

      <AdDrawer adId={selectedId} onClose={() => setSelectedId(null)} />
    </div>
  )
}
