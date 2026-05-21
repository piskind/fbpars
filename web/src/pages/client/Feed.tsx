import { useState, useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { clientApi } from '../../api/client'
import type { Ad } from '../../api/client'
import { AdDrawer } from '../../components/client/AdDrawer'


function mediaUrl(s3Url: string | null): string | null {
  if (!s3Url) return null
  const m = s3Url.match(/\/((?:m|ads)\/.+)$/)
  if (!m) return null
  return `/api/media/${m[1]}`
}

type Facets = {
  countries: string[]
  keywords: string[]
  verticals: string[]
  media_types: string[]
  ctas: string[]
}

export function ClientFeedPage() {
  const [country, setCountry] = useState('')
  const [keyword, setKeyword] = useState('')
  const [vertical, setVertical] = useState('')
  const [mediaType, setMediaType] = useState('')
  const [search, setSearch] = useState('')
  const [sort, setSort] = useState('newest')
  const [selectedId, setSelectedId] = useState<number | null>(null)

  const { data: facets } = useQuery({
    queryKey: ['feed-facets'],
    queryFn: async () => (await clientApi.get<Facets>('/feed/facets')).data,
  })

  const params = new URLSearchParams()
  params.set('limit', '100')
  params.set('sort', sort)
  if (country) params.set('country', country)
  if (keyword) params.set('keyword', keyword)
  if (vertical) params.set('vertical', vertical)
  if (mediaType) params.set('media_type', mediaType)
  if (search.trim()) params.set('search', search.trim())

  const { data, isLoading } = useQuery({
    queryKey: ['feed', country, keyword, vertical, mediaType, search, sort],
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

  const reset = () => {
    setCountry('')
    setKeyword('')
    setVertical('')
    setMediaType('')
    setSearch('')
    setSort('newest')
  }

  return (
    <div className="flex">
      <div className={`flex-1 transition-all ${selectedId ? 'lg:mr-[360px]' : ''}`}>
        <h1 className="text-2xl font-bold mb-4">
          Объявления{' '}
          <span className="text-gray-400 text-base font-normal">({data?.length ?? 0})</span>
        </h1>

        <div className="bg-white rounded-xl shadow p-4 mb-6 flex flex-wrap gap-3 items-end">
          <div>
            <label className="block text-xs text-gray-500 mb-1">Гео</label>
            <select value={country} onChange={(e) => setCountry(e.target.value)} className="px-3 py-2 border rounded-lg text-sm">
              <option value="">Все</option>
              {facets?.countries.map((c) => (<option key={c} value={c}>{c}</option>))}
            </select>
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">Ключ</label>
            <select value={keyword} onChange={(e) => setKeyword(e.target.value)} className="px-3 py-2 border rounded-lg text-sm">
              <option value="">Все</option>
              {facets?.keywords.map((k) => (<option key={k} value={k}>{k}</option>))}
            </select>
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">Вертикаль</label>
            <select value={vertical} onChange={(e) => setVertical(e.target.value)} className="px-3 py-2 border rounded-lg text-sm">
              <option value="">Все</option>
              {facets?.verticals.map((v) => (<option key={v} value={v}>{v}</option>))}
            </select>
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">Формат</label>
            <select value={mediaType} onChange={(e) => setMediaType(e.target.value)} className="px-3 py-2 border rounded-lg text-sm">
              <option value="">Все</option>
              {facets?.media_types.map((m) => (<option key={m} value={m}>{m}</option>))}
            </select>
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">Сортировка</label>
            <select value={sort} onChange={(e) => setSort(e.target.value)} className="px-3 py-2 border rounded-lg text-sm">
              <option value="newest">Сначала свежие</option>
              <option value="oldest">Сначала старые</option>
              <option value="days_desc">Дольше всего крутят</option>
              <option value="days_asc">Меньше всего крутят</option>
            </select>
          </div>
          <div className="flex-1 min-w-[200px]">
            <label className="block text-xs text-gray-500 mb-1">Поиск</label>
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="текст, page, ID, домен"
              className="w-full px-3 py-2 border rounded-lg text-sm"
            />
          </div>
          <button onClick={reset} className="px-3 py-2 text-sm text-gray-500 hover:text-gray-700">
            Сбросить
          </button>
        </div>

        {isLoading && <div>Загрузка...</div>}

        <div className="grid grid-cols-[repeat(auto-fill,minmax(200px,1fr))] gap-4">
          {data?.map((ad) => {
            const cre = ad.creatives.find((c) => c.s3_url) || ad.creatives[0]
            const url = cre ? mediaUrl(cre.s3_url) : null
            const isVideo = cre?.media_type?.toLowerCase() === 'video'
            const isSelected = selectedId === ad.id

            return (
              <div
                key={ad.id}
                onClick={() => setSelectedId(ad.id)}
                className={`bg-white rounded-xl shadow overflow-hidden flex flex-col cursor-pointer transition ${
                  isSelected
                    ? 'ring-2 ring-blue-500 shadow-md'
                    : 'hover:shadow-md'
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
                      {ad.page_id ? (
                        <a
                          href={`https://www.facebook.com/ads/library/?active_status=all&ad_type=all&country=ALL&view_all_page_id=${ad.page_id}`}
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
                      {ad.duplicates_count > 0 ? (
                        <span className="shrink-0 text-[10px] bg-orange-100 text-orange-700 px-1.5 py-0.5 rounded-full font-normal">
                          +{ad.duplicates_count}
                        </span>
                      ) : null}
                    </span>
                    <span className={`shrink-0 ml-2 ${ad.is_active ? 'text-green-600 text-xs' : 'text-gray-400 text-xs'}`}>
                      {ad.is_active ? 'Active' : 'Inactive'}
                    </span>
                  </div>
                  <div className="text-xs text-gray-500">
                    {ad.country} · {ad.keyword} · {ad.days_active}d
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