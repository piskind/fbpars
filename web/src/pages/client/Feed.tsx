import { useState, useEffect, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { clientApi } from '../../api/client'
import type { Ad } from '../../api/client'
import { AdDrawer } from '../../components/client/AdDrawer'
import { countryFlag } from '../../flags'

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
  keyword: string
  vertical: string
  mediaType: string
  cta: string
  platforms: string[]
  pageName: string
  domain: string
  linkContains: string
  startedFrom: string
  startedTo: string
  daysMin: string
  daysMax: string
  lastSeenFrom: string
  lastSeenTo: string
  search: string
  sort: string
  language: string
  leadForm: string
  appStore: string
  ecomPlatform: string
  ipQuery: string
}

const emptyFilters: Filters = {
  countries: [],
  keyword: '',
  vertical: '',
  mediaType: '',
  cta: '',
  platforms: [],
  pageName: '',
  domain: '',
  linkContains: '',
  startedFrom: '',
  startedTo: '',
  daysMin: '',
  daysMax: '',
  lastSeenFrom: '',
  lastSeenTo: '',
  search: '',
  sort: 'newest',
  language: '',
  leadForm: '',
  appStore: '',
  ecomPlatform: '',
  ipQuery: '',
}

export function ClientFeedPage() {
  const [draft, setDraft] = useState<Filters>(emptyFilters)
  const [applied, setApplied] = useState<Filters>(emptyFilters)
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [showFine, setShowFine] = useState(false)
  const [selectedId, setSelectedId] = useState<number | null>(null)

  const { data: facets } = useQuery({
    queryKey: ['feed-facets'],
    queryFn: async () => (await clientApi.get<Facets>('/feed/facets')).data,
  })

  const params = useMemo(() => {
    const p = new URLSearchParams()
    p.set('limit', '1000')
    p.set('sort', applied.sort)
    applied.countries.forEach((c) => p.append('countries', c))
    if (applied.keyword) p.set('keyword', applied.keyword)
    if (applied.vertical) p.set('vertical', applied.vertical)
    if (applied.mediaType) p.set('media_type', applied.mediaType)
    if (applied.cta) p.set('cta', applied.cta)
    applied.platforms.forEach((pl) => p.append('platforms', pl))
    if (applied.pageName) p.set('page_name', applied.pageName)
    if (applied.domain) p.set('domain', applied.domain)
    if (applied.linkContains) p.set('link_contains', applied.linkContains)
    if (applied.startedFrom) p.set('started_from', applied.startedFrom)
    if (applied.startedTo) p.set('started_to', applied.startedTo)
    if (applied.daysMin) p.set('days_active_min', applied.daysMin)
    if (applied.daysMax) p.set('days_active_max', applied.daysMax)
    if (applied.lastSeenFrom) p.set('last_seen_from', applied.lastSeenFrom)
    if (applied.lastSeenTo) p.set('last_seen_to', applied.lastSeenTo)
    if (applied.language) p.set('language', applied.language)
    if (applied.leadForm) p.set('lead_form', applied.leadForm)
    if (applied.appStore) p.set('app_store', applied.appStore)
    if (applied.ecomPlatform) p.set('ecom_platform', applied.ecomPlatform)
    if (applied.ipQuery) p.set('ip', applied.ipQuery)
    if (applied.search.trim()) p.set('search', applied.search.trim())
    return p
  }, [applied])

  const { data, isLoading } = useQuery({
    queryKey: ['feed', params.toString()],
    queryFn: async () =>
      (await clientApi.get<Ad[]>(`/feed?${params.toString()}`)).data,
  })

  useEffect(() => {
    const handler = (e: Event) => {
      const id = (e as CustomEvent).detail as number
      setSelectedId(id)
    }
    window.addEventListener('drawer:select', handler)
    return () => window.removeEventListener('drawer:select', handler)
  }, [])

  const apply = () => setApplied(draft)
  const reset = () => {
    setDraft(emptyFilters)
    setApplied(emptyFilters)
  }

  const toggleCountry = (c: string) => {
    setDraft((d) => ({
      ...d,
      countries: d.countries.includes(c)
        ? d.countries.filter((x) => x !== c)
        : [...d.countries, c],
    }))
  }

  const togglePlatform = (p: string) => {
    setDraft((d) => ({
      ...d,
      platforms: d.platforms.includes(p)
        ? d.platforms.filter((x) => x !== p)
        : [...d.platforms, p],
    }))
  }

  return (
    <div className="flex">
      <div className={`flex-1 transition-all ${selectedId ? 'lg:mr-[360px]' : ''}`}>
        <h1 className="text-2xl font-bold mb-4">
          Объявления{' '}
          <span className="text-gray-400 text-base font-normal">({data?.length ?? 0})</span>
        </h1>

        {/* Основной блок фильтров */}
        <div className="bg-white rounded-xl shadow p-4 mb-3 space-y-3">
          <div className="flex flex-wrap gap-3 items-end">
            <div className="flex-1 min-w-[200px]">
              <label className="block text-xs text-gray-500 mb-1">Поиск</label>
              <input
                value={draft.search}
                onChange={(e) => setDraft({ ...draft, search: e.target.value })}
                onKeyDown={(e) => e.key === 'Enter' && apply()}
                placeholder="текст, page, ID, домен"
                className="w-full px-3 py-2 border rounded-lg text-sm"
              />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">Ключ</label>
              <select
                value={draft.keyword}
                onChange={(e) => setDraft({ ...draft, keyword: e.target.value })}
                className="px-3 py-2 border rounded-lg text-sm"
              >
                <option value="">Все</option>
                {facets?.keywords.map((k) => <option key={k} value={k}>{k}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">Вертикаль</label>
              <select
                value={draft.vertical}
                onChange={(e) => setDraft({ ...draft, vertical: e.target.value })}
                className="px-3 py-2 border rounded-lg text-sm"
              >
                <option value="">Все</option>
                {facets?.verticals.map((v) => <option key={v} value={v}>{v}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">Сортировка</label>
              <select
                value={draft.sort}
                onChange={(e) => setDraft({ ...draft, sort: e.target.value })}
                className="px-3 py-2 border rounded-lg text-sm"
              >
                <option value="newest">Сначала свежие</option>
                <option value="oldest">Сначала старые</option>
                <option value="days_desc">Дольше крутят</option>
                <option value="days_asc">Меньше крутят</option>
              </select>
            </div>
          </div>

          {/* Страны - мульти */}
          <div>
            <label className="block text-xs text-gray-500 mb-1">Страны</label>
            <div className="flex flex-wrap gap-1.5">
              {facets?.countries.map((c) => {
                const on = draft.countries.includes(c)
                return (
                  <button
                    key={c}
                    onClick={() => toggleCountry(c)}
                    className={`px-2.5 py-1 rounded-full text-xs border transition ${
                      on
                        ? 'bg-blue-600 text-white border-blue-600'
                        : 'bg-white text-gray-700 border-gray-300 hover:border-gray-400'
                    }`}
                  >
                    {countryFlag(c)} {c}
                  </button>
                )
              })}
            </div>
          </div>

          {/* Даты и активность */}
          <div className="flex flex-wrap gap-3 items-end">
            <div>
              <label className="block text-xs text-gray-500 mb-1">Дата создания от</label>
              <input
                type="date"
                value={draft.startedFrom}
                onChange={(e) => setDraft({ ...draft, startedFrom: e.target.value })}
                className="px-3 py-2 border rounded-lg text-sm"
              />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">до</label>
              <input
                type="date"
                value={draft.startedTo}
                onChange={(e) => setDraft({ ...draft, startedTo: e.target.value })}
                className="px-3 py-2 border rounded-lg text-sm"
              />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">Активность дней от</label>
              <input
                type="number"
                min="0"
                value={draft.daysMin}
                onChange={(e) => setDraft({ ...draft, daysMin: e.target.value })}
                className="w-20 px-3 py-2 border rounded-lg text-sm"
              />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">до</label>
              <input
                type="number"
                min="0"
                value={draft.daysMax}
                onChange={(e) => setDraft({ ...draft, daysMax: e.target.value })}
                className="w-20 px-3 py-2 border rounded-lg text-sm"
              />
            </div>
          </div>

          {/* Раскрывашка Подфильтры */}
          <div className="border-t pt-3">
            <button
              onClick={() => setShowAdvanced((v) => !v)}
              className="text-sm text-blue-600 hover:underline"
            >
              {showAdvanced ? '▼' : '▶'} Настройки объявлений
            </button>
            {showAdvanced && (
              <div className="mt-3 flex flex-wrap gap-3 items-end">
                <div>
                  <label className="block text-xs text-gray-500 mb-1">Формат объявлений</label>
                  <select
                    value={draft.mediaType}
                    onChange={(e) => setDraft({ ...draft, mediaType: e.target.value })}
                    className="px-3 py-2 border rounded-lg text-sm"
                  >
                    <option value="">Все</option>
                    {facets?.media_types.map((m) => <option key={m} value={m}>{m}</option>)}
                  </select>
                </div>
                <div>
                  <label className="block text-xs text-gray-500 mb-1">CTA</label>
                  <select
                    value={draft.cta}
                    onChange={(e) => setDraft({ ...draft, cta: e.target.value })}
                    className="px-3 py-2 border rounded-lg text-sm"
                  >
                    <option value="">Все</option>
                    {facets?.ctas.map((c) => <option key={c} value={c}>{c}</option>)}
                  </select>
                </div>
                {facets?.platforms && facets.platforms.length > 0 && (
                  <div>
                    <label className="block text-xs text-gray-500 mb-1">Плейсмент</label>
                    <div className="flex flex-wrap gap-1.5">
                      {facets.platforms.map((p) => {
                        const on = draft.platforms.includes(p)
                        return (
                          <button
                            key={p}
                            onClick={() => togglePlatform(p)}
                            className={`px-2.5 py-1 rounded-full text-xs border transition ${
                              on
                                ? 'bg-blue-600 text-white border-blue-600'
                                : 'bg-white text-gray-700 border-gray-300 hover:border-gray-400'
                            }`}
                          >
                            {p}
                          </button>
                        )
                      })}
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Раскрывашка Тонкие настройки */}
          <div className="border-t pt-3">
            <button
              onClick={() => setShowFine((v) => !v)}
              className="text-sm text-blue-600 hover:underline"
            >
              {showFine ? '▼' : '▶'} Тонкие настройки
            </button>
            {showFine && (
              <div className="mt-3 flex flex-wrap gap-3 items-end">
                <div>
                  <label className="block text-xs text-gray-500 mb-1">Фанпейдж</label>
                  <input
                    value={draft.pageName}
                    onChange={(e) => setDraft({ ...draft, pageName: e.target.value })}
                    placeholder="ID или название"
                    className="px-3 py-2 border rounded-lg text-sm w-56"
                  />
                </div>
                <div>
                  <label className="block text-xs text-gray-500 mb-1">Домен</label>
                  <input
                    value={draft.domain}
                    onChange={(e) => setDraft({ ...draft, domain: e.target.value })}
                    placeholder="example.com"
                    className="px-3 py-2 border rounded-lg text-sm w-48"
                  />
                </div>
                <div>
                  <label className="block text-xs text-gray-500 mb-1">В ссылке</label>
                  <input
                    value={draft.linkContains}
                    onChange={(e) => setDraft({ ...draft, linkContains: e.target.value })}
                    placeholder="фрагмент URL"
                    className="px-3 py-2 border rounded-lg text-sm w-48"
                  />
                </div>
                <div>
                  <label className="block text-xs text-gray-500 mb-1">Посл. активность от</label>
                  <input
                    type="date"
                    value={draft.lastSeenFrom}
                    onChange={(e) => setDraft({ ...draft, lastSeenFrom: e.target.value })}
                    className="px-3 py-2 border rounded-lg text-sm"
                  />
                </div>
                <div>
                  <label className="block text-xs text-gray-500 mb-1">до</label>
                  <input
                    type="date"
                    value={draft.lastSeenTo}
                    onChange={(e) => setDraft({ ...draft, lastSeenTo: e.target.value })}
                    className="px-3 py-2 border rounded-lg text-sm"
                  />
                </div>
                <div>
                  <label className="block text-xs text-gray-500 mb-1">Язык</label>
                  <select
                    value={draft.language}
                    onChange={(e) => setDraft({ ...draft, language: e.target.value })}
                    className="px-3 py-2 border rounded-lg text-sm"
                  >
                    <option value="">Все</option>
                    {facets?.languages?.map((l) => <option key={l} value={l}>{l.toUpperCase()}</option>)}
                  </select>
                </div>
                <div>
                  <label className="block text-xs text-gray-500 mb-1">Приложение</label>
                  <select
                    value={draft.appStore}
                    onChange={(e) => setDraft({ ...draft, appStore: e.target.value })}
                    className="px-3 py-2 border rounded-lg text-sm"
                  >
                    <option value="">Все</option>
                    {facets?.app_stores?.map((a) => <option key={a} value={a}>{a}</option>)}
                  </select>
                </div>
                <div>
                  <label className="block text-xs text-gray-500 mb-1">E-com платформа</label>
                  <select
                    value={draft.ecomPlatform}
                    onChange={(e) => setDraft({ ...draft, ecomPlatform: e.target.value })}
                    className="px-3 py-2 border rounded-lg text-sm"
                  >
                    <option value="">Все</option>
                    {facets?.ecom_platforms?.map((e) => <option key={e} value={e}>{e}</option>)}
                  </select>
                </div>
                <div>
                  <label className="block text-xs text-gray-500 mb-1">Lead-form</label>
                  <select
                    value={draft.leadForm}
                    onChange={(e) => setDraft({ ...draft, leadForm: e.target.value })}
                    className="px-3 py-2 border rounded-lg text-sm"
                  >
                    <option value="">Все</option>
                    <option value="true">Только лид-формы</option>
                    <option value="false">Без лид-форм</option>
                  </select>
                </div>
                <div>
                  <label className="block text-xs text-gray-500 mb-1">IP</label>
                  <input
                    value={draft.ipQuery}
                    onChange={(e) => setDraft({ ...draft, ipQuery: e.target.value })}
                    placeholder="например 31.31.196.208"
                    className="px-3 py-2 border rounded-lg text-sm w-44"
                  />
                </div>
              </div>
            )}
          </div>

          <div className="flex gap-2 pt-2 border-t">
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

        {isLoading && <div>Загрузка...</div>}

        <div className="grid grid-cols-[repeat(auto-fill,minmax(200px,1fr))] gap-4">
          {data?.map((ad) => {
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
                    <video src={url} className="w-full h-full object-cover" muted preload="metadata" />
                  ) : url ? (
                    <img src={url} alt="" className="w-full h-full object-cover" />
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
                    <span className={`shrink-0 ml-2 ${ad.is_active ? 'text-green-600 text-xs' : 'text-gray-400 text-xs'}`}>
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

        {!isLoading && data?.length === 0 && (
          <div className="text-gray-400 mt-12 text-center">Ничего не найдено</div>
        )}
      </div>

      <AdDrawer adId={selectedId} onClose={() => setSelectedId(null)} />
    </div>
  )
}