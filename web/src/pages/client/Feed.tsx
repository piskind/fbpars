import React, { useState, useEffect, useMemo, useRef } from 'react'
import { useQuery, useInfiniteQuery } from '@tanstack/react-query'
import { clientApi } from '../../api/client'
import type { Ad } from '../../api/client'
import { AdDrawer } from '../../components/client/AdDrawer'
import { DateRangePicker } from '../../components/client/DateRangePicker'
import { countryFlag } from '../../flags'
import { Settings, SlidersHorizontal, ChevronDown, Search, Calendar, Download } from 'lucide-react'

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

function fmtDate(s: string | null | undefined): string {
  if (!s) return '—'
  return new Date(s).toLocaleDateString('ru-RU')
}

function downloadFile(url: string, name = '') {
  const a = document.createElement('a')
  a.href = url
  a.download = name
  a.target = '_blank'
  document.body.appendChild(a)
  a.click()
  a.remove()
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
  country: string
  search: string
  searchMode: string
  countryCount: string
  startedFrom: string
  startedTo: string
  daysMin: string
  daysMax: string
  vertical: string
  partners: string[]
  sort: string
  isActive: string
  // Ad settings card
  mediaType: string
  cta: string
  platforms: string[]
  leadForm: string
  reachMin: string
  spendMin: string
  hasEuData: string
  // Fine settings card
  pageName: string
  appLink: string
  domain: string
  linkContains: string
  appStore: string
  lastSeenFrom: string
  ecomPlatform: string
  ipQuery: string
  language: string
}

const emptyFilters: Filters = {
  country: '',
  search: '',
  searchMode: 'exact',
  countryCount: '',
  startedFrom: '',
  startedTo: '',
  daysMin: '',
  daysMax: '',
  vertical: '',
  partners: [],
  sort: 'newest',
  isActive: '',
  mediaType: '',
  cta: '',
  platforms: [],
  leadForm: '',
  reachMin: '',
  spendMin: '',
  hasEuData: '',
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

// Вертикали. active=false → заглушка «в разработке».
const VERTICALS: { key: string; label: string; active: boolean }[] = [
  { key: 'nutra', label: 'Nutra', active: true },
  { key: 'gambling', label: 'Gambling', active: true },
  { key: 'apps', label: 'Apps', active: false },
  { key: 'ecommerce', label: 'E-commerce', active: false },
]

// Подкатегории (захардкожены — фильтр по подкатегории появится с парсером).
const VERTICAL_CHIPS = [
  'All', 'Uncategorized', 'Aviator', 'Bomb defuse', 'Book of dead', 'Book of ra',
  'Chicken road', 'Chicken subway', 'Coin strike', 'Energy coins', 'Energy joker',
  'Fire joker', 'Gates of olympus', 'Joker stoker', 'Jokers jewels', 'Penalty duel',
  'Pink joker', 'Plinko', 'Royal joker', 'Sugar rush', 'Sun of egypt', 'Sweet bonanza',
  'Tower rush',
]

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
  selected: string | null
  onChange: (v: string | null) => void
}) {
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState('')
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false)
        setSearch('')
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  const label = selected ? `${countryFlag(selected)} ${selected}` : 'Все'
  const filtered = search
    ? options.filter((c) => c.toLowerCase().includes(search.toLowerCase()))
    : options

  const select = (c: string | null) => {
    onChange(c)
    setOpen(false)
    setSearch('')
  }

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
        <div className="absolute z-50 mt-1 bg-white border rounded-lg shadow-lg w-52">
          <div className="p-2 border-b">
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Поиск..."
              className="w-full px-2 py-1 text-sm border rounded"
              autoFocus
            />
          </div>
          <div className="max-h-52 overflow-y-auto">
            <button
              type="button"
              onClick={() => select(null)}
              className={`w-full text-left px-3 py-1.5 text-sm hover:bg-gray-50 ${!selected ? 'bg-blue-50 text-blue-700 font-medium' : ''}`}
            >
              Все
            </button>
            {filtered.map((c) => (
              <button
                key={c}
                type="button"
                onClick={() => select(selected === c ? null : c)}
                className={`w-full text-left px-3 py-1.5 text-sm hover:bg-gray-50 flex items-center justify-between ${selected === c ? 'bg-blue-50 text-blue-700 font-medium' : ''}`}
              >
                <span>{countryFlag(c)} {c}</span>
                {selected === c && <span className="text-blue-500 text-xs">✓</span>}
              </button>
            ))}
          </div>
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
  const [openVertical, setOpenVertical] = useState<string | null>(null)
  const [subcat, setSubcat] = useState('All')
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const sentinelRef = useRef<HTMLDivElement>(null)

  const { data: facets } = useQuery({
    queryKey: ['feed-facets'],
    queryFn: async () => (await clientApi.get<Facets>('/feed/facets')).data,
  })

  const { data: partnersData } = useQuery({
    queryKey: ['feed-partners'],
    queryFn: async () =>
      (await clientApi.get<{ partners: string[] }>('/feed/partners')).data,
  })

  const { data: vertCounts } = useQuery({
    queryKey: ['feed-vertical-counts'],
    queryFn: async () =>
      (await clientApi.get<{ counts: Record<string, number> }>('/feed/vertical-counts')).data,
  })

  const baseParams = useMemo(() => {
    const p = new URLSearchParams()
    p.set('sort', applied.sort)
    if (applied.country) p.set('country', applied.country)
    if (applied.vertical) p.set('vertical', applied.vertical)
    if (applied.search.trim()) {
      p.set('search', applied.search.trim())
      p.set('search_mode', applied.searchMode)
    }
    applied.partners.forEach((pt) => p.append('partner', pt))
    if (applied.countryCount) p.set('country_count', applied.countryCount)
    if (applied.startedFrom) p.set('started_from', applied.startedFrom)
    if (applied.startedTo) p.set('started_to', applied.startedTo)
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
    if (applied.reachMin) p.set('reach_min', applied.reachMin)
    else if (applied.hasEuData === 'yes') p.set('reach_min', '1')
    if (applied.spendMin) p.set('spend_min', applied.spendMin)
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

  const { data: countData } = useQuery({
    queryKey: ['feed-count', baseParams.toString()],
    queryFn: async () =>
      (await clientApi.get<{ total: number }>(`/feed/count?${baseParams.toString()}`)).data,
  })
  const total = countData?.total

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
          <span className="text-gray-400 text-base font-normal">
            ({total != null ? total.toLocaleString('ru-RU') : ads.length})
          </span>
        </h1>

        {/* ── Filter panel ── */}
        <div className="bg-white rounded-xl shadow p-4 mb-3">

          {/* Top row: single-line filter bar */}
          <div className="flex flex-wrap gap-3 items-end">
            <div className="flex-1 min-w-[160px]">
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
              <label className="block text-xs text-gray-500 mb-1">Тип поиска</label>
              <div className="relative">
                <select
                  value={draft.searchMode}
                  onChange={(e) => set({ searchMode: e.target.value })}
                  className="appearance-none px-3 py-2 pr-8 border rounded-lg text-sm bg-white"
                >
                  <option value="exact">Точный</option>
                  <option value="broad">Широкий</option>
                </select>
                <ChevronDown className="absolute right-2.5 top-2.5 w-3.5 h-3.5 text-gray-400 pointer-events-none" />
              </div>
            </div>

            <div>
              <label className="block text-xs text-gray-500 mb-1">Страны</label>
              <CountryDropdown
                options={facets?.countries ?? []}
                selected={draft.country || null}
                onChange={(c) => set({ country: c ?? '' })}
              />
            </div>

            <div>
              <label className="block text-xs text-gray-500 mb-1" title="Число стран показа (по ЕС-данным)">
                Кол-во стран
              </label>
              <input
                type="number"
                min="0"
                value={draft.countryCount}
                onChange={(e) => set({ countryCount: e.target.value })}
                placeholder="—"
                className="w-20 px-3 py-2 border rounded-lg text-sm"
              />
            </div>

            <div>
              <label className="block text-xs text-gray-500 mb-1">Дата создания</label>
              <DateRangePicker
                from={draft.startedFrom}
                to={draft.startedTo}
                onChange={(f, t) => set({ startedFrom: f, startedTo: t })}
              />
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

            <div>
              <label className="block text-xs text-gray-500 mb-1">Активность дней</label>
              <div className="flex items-center gap-1">
                <input
                  type="number"
                  min="0"
                  value={draft.daysMin}
                  onChange={(e) => set({ daysMin: e.target.value })}
                  placeholder="от"
                  className="w-16 px-2 py-2 border rounded-lg text-sm"
                />
                <span className="text-gray-400 text-xs">–</span>
                <input
                  type="number"
                  min="0"
                  value={draft.daysMax}
                  onChange={(e) => set({ daysMax: e.target.value })}
                  placeholder="до"
                  className="w-16 px-2 py-2 border rounded-lg text-sm"
                />
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

            <div className="min-w-[140px]">
              <label className="block text-xs text-gray-500 mb-1">Партнёрка</label>
              <MultiSelectDropdown
                options={partnersData?.partners ?? []}
                selected={draft.partners}
                onChange={(partners) => set({ partners })}
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

            <button
              onClick={apply}
              className="px-5 py-2 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700"
            >
              Найти
            </button>
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

                  <FieldRow label="EU данные">
                    <SelectField value={draft.hasEuData} onChange={(v) => set({ hasEuData: v })}>
                      <option value="">Все</option>
                      <option value="yes">Только с охватом</option>
                    </SelectField>
                  </FieldRow>

                  <FieldRow label="Мин. охват">
                    <input
                      type="number"
                      min="0"
                      value={draft.reachMin}
                      onChange={(e) => set({ reachMin: e.target.value })}
                      placeholder="напр. 10000"
                      className="w-full px-3 py-2 border rounded-lg text-sm"
                    />
                  </FieldRow>

                  <FieldRow label="Мин. спенд $">
                    <input
                      type="number"
                      min="0"
                      value={draft.spendMin}
                      onChange={(e) => set({ spendMin: e.target.value })}
                      placeholder="напр. 1000"
                      className="w-full px-3 py-2 border rounded-lg text-sm"
                    />
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

          {/* Вертикали */}
          <div className="mt-3 pt-3 border-t">
            <div className="flex flex-wrap gap-2">
              {VERTICALS.map((v) => {
                const selected = draft.vertical === v.key
                const cnt = vertCounts?.counts?.[v.key]
                return (
                  <button
                    key={v.key}
                    type="button"
                    disabled={!v.active}
                    onClick={() => {
                      set({ vertical: selected ? '' : v.key })
                      setOpenVertical(openVertical === v.key ? null : v.key)
                      setSubcat('All')
                    }}
                    title={v.active ? undefined : 'В разработке'}
                    className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm border transition ${
                      !v.active
                        ? 'opacity-50 cursor-not-allowed bg-gray-50 text-gray-400'
                        : selected
                        ? 'bg-blue-600 text-white border-blue-600'
                        : 'bg-white hover:border-gray-400'
                    }`}
                  >
                    {v.label}
                    {!v.active && <span className="text-[10px]">🛠</span>}
                    {v.active && cnt != null && (
                      <span
                        className={`text-[10px] px-1.5 py-0.5 rounded-full ${
                          selected ? 'bg-white/20' : 'bg-gray-100 text-gray-500'
                        }`}
                      >
                        {cnt.toLocaleString('ru-RU')}
                      </span>
                    )}
                  </button>
                )
              })}
            </div>

            {/* Чипсы-подкатегории */}
            {openVertical && VERTICALS.find((v) => v.key === openVertical)?.active && (
              <div className="flex flex-wrap gap-1.5 mt-3">
                {VERTICAL_CHIPS.map((c) => (
                  <button
                    key={c}
                    type="button"
                    onClick={() => setSubcat(c)}
                    className={`px-2.5 py-1 rounded-full text-xs border transition ${
                      subcat === c
                        ? 'bg-blue-50 text-blue-700 border-blue-200'
                        : 'bg-white text-gray-600 hover:border-gray-300'
                    }`}
                  >
                    {c}
                  </button>
                ))}
              </div>
            )}
          </div>

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
          <div className="grid grid-cols-[repeat(auto-fill,minmax(200px,1fr))] gap-4 mb-4">
            {Array.from({ length: 12 }).map((_, i) => (
              <div key={i} className="bg-white rounded-xl shadow overflow-hidden animate-pulse">
                <div className="w-full aspect-square bg-gray-200" />
                <div className="p-3 space-y-2">
                  <div className="h-3 bg-gray-200 rounded w-3/4" />
                  <div className="h-3 bg-gray-200 rounded w-1/2" />
                </div>
              </div>
            ))}
          </div>
        )}

        <div className="grid grid-cols-[repeat(auto-fill,minmax(200px,1fr))] gap-4">
          {ads.map((ad) => {
            const cre = ad.creatives.find((c) => c.s3_url) || ad.creatives[0]
            const url = cre ? mediaUrl(cre.s3_url) : null
            const isVideo = cre?.media_type?.toLowerCase() === 'video'
            const isSelected = selectedId === ad.id
            const pageFbUrl = adsLibraryUrl(ad.page_id)
            const days = Math.max(1, ad.days_active)
            const usedIn = ad.used_in_ads_count ?? ad.duplicates_count
            const dayTip =
              `Последний раз объявление было активно: ${fmtDate(ad.last_seen_at)}\n` +
              `Всего объявление было активным: ${days}\n` +
              `Первый раз объявление получено: ${fmtDate(ad.first_seen_at)}`

            return (
              <div
                key={ad.id}
                onClick={() => setSelectedId(ad.id)}
                className={`bg-white rounded-xl shadow overflow-hidden flex flex-col cursor-pointer transition ${
                  isSelected ? 'ring-2 ring-blue-500 shadow-md' : 'hover:shadow-md'
                }`}
              >
                <div className="group relative w-full aspect-square bg-gray-100 flex items-center justify-center overflow-hidden">
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

                  {/* Кнопка скачивания (слева сверху, на hover) */}
                  {url && (
                    <button
                      type="button"
                      title="Скачать креатив"
                      onClick={(e) => {
                        e.stopPropagation()
                        downloadFile(url, `ad-${ad.id}`)
                      }}
                      className="absolute top-2 left-2 opacity-0 group-hover:opacity-100 transition w-7 h-7 rounded-lg bg-blue-600 text-white flex items-center justify-center shadow hover:bg-blue-700"
                    >
                      <Download className="w-3.5 h-3.5" />
                    </button>
                  )}

                  {/* Видео: бейдж + плеер по центру */}
                  {isVideo && (
                    <>
                      <div className="absolute top-2 right-2 flex items-center gap-1 bg-black/60 text-white text-[10px] px-1.5 py-0.5 rounded">
                        <span>▶</span>
                        <span>видео</span>
                      </div>
                      <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
                        <div className="w-10 h-10 rounded-full bg-black/40 flex items-center justify-center">
                          <span className="text-white text-lg leading-none">▶</span>
                        </div>
                      </div>
                    </>
                  )}

                  {/* Бейдж дня + статус (справа внизу) */}
                  <div
                    className="absolute bottom-2 right-2 flex items-center gap-1.5"
                    title={dayTip}
                  >
                    <div className="flex items-center gap-1 bg-black/70 text-white text-[10px] px-1.5 py-0.5 rounded">
                      <Calendar className="w-3 h-3" />
                      <span>{days} день</span>
                    </div>
                    <span
                      className={`w-2.5 h-2.5 rounded-full border border-white/50 ${
                        ad.is_active ? 'bg-green-500' : 'bg-orange-500'
                      }`}
                    />
                  </div>
                </div>

                <div className="p-3 flex-1 flex flex-col">
                  <div className="flex items-center mb-1 text-sm">
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
                      {usedIn > 0 && (
                        <span
                          className="shrink-0 text-[10px] bg-orange-100 text-orange-700 px-1.5 py-0.5 rounded-full font-normal"
                          title={`Это медиа встречается ещё в ${usedIn} объявлениях`}
                        >
                          +{usedIn}
                        </span>
                      )}
                      {ad.partner && (
                        <span className="shrink-0 text-[10px] bg-indigo-100 text-indigo-700 px-1.5 py-0.5 rounded-full font-normal">
                          {ad.partner}
                        </span>
                      )}
                    </span>
                  </div>
                  <div className="text-xs text-gray-500">
                    {countryFlag(ad.country)} {ad.country} · {ad.keyword}
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
