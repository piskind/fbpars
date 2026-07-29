import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api, adMedia } from '../api/client'
import type { ModerationItem } from '../api/client'

export function ModerationPage() {
  return <ModerationList status="pending" title="Модерация" />
}

export function TrashPage() {
  return <ModerationList status="rejected" title="Мусор" />
}

type Facets = { countries: string[]; keywords: string[] }
type ModerationListOut = { items: ModerationItem[]; total: number }

const PAGE_SIZE = 50

function ModerationList({ status, title }: { status: string; title: string }) {
  const qc = useQueryClient()

  const [country, setCountry] = useState('')
  const [keyword, setKeyword] = useState('')
  const [isActive, setIsActive] = useState<string>('')
  const [hasMedia, setHasMedia] = useState<string>('')
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [errorMsg, setErrorMsg] = useState<string | null>(null)

  const { data: facets } = useQuery({
    queryKey: ['moderation-facets'],
    queryFn: async () => (await api.get<Facets>('/moderation/facets')).data,
  })

  const queryKey = ['moderation', status, country, keyword, isActive, hasMedia, search, page]

  const params = new URLSearchParams()
  params.set('status', status)
  params.set('limit', String(PAGE_SIZE))
  params.set('offset', String((page - 1) * PAGE_SIZE))
  if (country) params.set('country', country)
  if (keyword) params.set('keyword', keyword)
  if (isActive) params.set('is_active', isActive)
  if (hasMedia) params.set('has_media', hasMedia)
  if (search.trim()) params.set('search', search.trim())

  const { data, isLoading } = useQuery({
    queryKey,
    queryFn: async () =>
      (await api.get<ModerationListOut>(`/moderation?${params.toString()}`)).data,
  })

  const totalPages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1

  const review = useMutation({
    mutationFn: async (args: { id: number; newStatus: 'approved' | 'rejected'; reason?: string }) => {
      await api.post(`/moderation/${args.id}`, {
        status: args.newStatus,
        reject_reason: args.reason,
      })
    },
    onMutate: async ({ id }) => {
      setErrorMsg(null)
      await qc.cancelQueries({ queryKey })
      const prev = qc.getQueryData<ModerationListOut>(queryKey)
      qc.setQueryData<ModerationListOut>(queryKey, (old) => {
        if (!old) return old
        return {
          items: old.items.filter((i) => i.id !== id),
          total: Math.max(0, old.total - 1),
        }
      })
      return { prev }
    },
    onError: (_err, _args, ctx) => {
      qc.setQueryData(queryKey, ctx?.prev)
      setErrorMsg('Ошибка при сохранении. Попробуйте ещё раз.')
    },
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ['moderation-stats'] })
    },
  })

  const resetFilters = () => {
    setCountry('')
    setKeyword('')
    setIsActive('')
    setHasMedia('')
    setSearch('')
    setPage(1)
  }

  const goPage = (p: number) => setPage(Math.max(1, Math.min(p, totalPages)))

  return (
    <div>
      <h1 className="text-2xl font-bold mb-4">
        {title}{' '}
        <span className="text-gray-400 text-base font-normal">
          ({data?.total ?? 0})
        </span>
      </h1>

      {errorMsg && (
        <div className="mb-4 px-4 py-2 bg-red-50 text-red-700 rounded-lg text-sm flex justify-between">
          {errorMsg}
          <button onClick={() => setErrorMsg(null)} className="ml-4 font-bold">✕</button>
        </div>
      )}

      <div className="bg-white rounded-xl shadow p-4 mb-6 flex flex-wrap gap-3 items-end">
        <div>
          <label className="block text-xs text-gray-500 mb-1">Гео</label>
          <select
            value={country}
            onChange={(e) => { setCountry(e.target.value); setPage(1) }}
            className="px-3 py-2 border rounded-lg text-sm"
          >
            <option value="">Все</option>
            {facets?.countries.map((c) => (<option key={c} value={c}>{c}</option>))}
          </select>
        </div>

        <div>
          <label className="block text-xs text-gray-500 mb-1">Ключ</label>
          <select
            value={keyword}
            onChange={(e) => { setKeyword(e.target.value); setPage(1) }}
            className="px-3 py-2 border rounded-lg text-sm"
          >
            <option value="">Все</option>
            {facets?.keywords.map((k) => (<option key={k} value={k}>{k}</option>))}
          </select>
        </div>

        <div>
          <label className="block text-xs text-gray-500 mb-1">Активность</label>
          <select
            value={isActive}
            onChange={(e) => { setIsActive(e.target.value); setPage(1) }}
            className="px-3 py-2 border rounded-lg text-sm"
          >
            <option value="">Все</option>
            <option value="true">Active</option>
            <option value="false">Inactive</option>
          </select>
        </div>

        <div>
          <label className="block text-xs text-gray-500 mb-1">Медиа</label>
          <select
            value={hasMedia}
            onChange={(e) => { setHasMedia(e.target.value); setPage(1) }}
            className="px-3 py-2 border rounded-lg text-sm"
          >
            <option value="">Все</option>
            <option value="true">С медиа</option>
            <option value="false">Без медиа</option>
          </select>
        </div>

        <div className="flex-1 min-w-[200px]">
          <label className="block text-xs text-gray-500 mb-1">Поиск</label>
          <input
            value={search}
            onChange={(e) => { setSearch(e.target.value); setPage(1) }}
            placeholder="текст, page, ID, домен"
            className="w-full px-3 py-2 border rounded-lg text-sm"
          />
        </div>

        <button onClick={resetFilters} className="px-3 py-2 text-sm text-gray-500 hover:text-gray-700">
          Сбросить
        </button>
      </div>

      {isLoading && <div>Загрузка...</div>}

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5 gap-4">
        {data?.items.map((item) => (
          <Card
            key={item.id}
            item={item}
            onApprove={() => review.mutate({ id: item.id, newStatus: 'approved' })}
            onReject={() => {
              const reason = prompt('Причина отклонения (необязательно):') || undefined
              review.mutate({ id: item.id, newStatus: 'rejected', reason })
            }}
            showActions={status === 'pending'}
          />
        ))}
      </div>

      {!isLoading && data?.items.length === 0 && (
        <div className="text-gray-400 mt-12 text-center">Пусто</div>
      )}

      {/* Pagination */}
      {(data?.total ?? 0) > PAGE_SIZE && (
        <div className="flex items-center justify-center gap-4 mt-8">
          <button
            onClick={() => goPage(page - 1)}
            disabled={page <= 1}
            className="px-4 py-2 rounded-lg border text-sm disabled:opacity-40 hover:bg-gray-50"
          >
            ← Назад
          </button>
          <span className="text-sm text-gray-600">
            Страница {page} из {totalPages} (всего {data?.total ?? 0})
          </span>
          <button
            onClick={() => goPage(page + 1)}
            disabled={page >= totalPages}
            className="px-4 py-2 rounded-lg border text-sm disabled:opacity-40 hover:bg-gray-50"
          >
            Вперёд →
          </button>
        </div>
      )}
    </div>
  )
}

function Card(props: {
  item: ModerationItem
  onApprove: () => void
  onReject: () => void
  showActions: boolean
}) {
  const { item, onApprove, onReject, showActions } = props
  const { ad } = item
  // Медиа — прямые FB CDN URL (video_urls/image_urls/poster_urls), как в клиентском фиде.
  // Legacy-таблица creatives пустая, поэтому старый рендер из неё показывал «нет медиа».
  const { url, poster, isVideo } = adMedia(ad)

  const openLink = () => {
    if (ad.link_url) window.open(ad.link_url, '_blank', 'noopener,noreferrer')
  }

  return (
    <div className="bg-white rounded-xl shadow overflow-hidden flex flex-col">
      <div className="w-[300px] h-[300px] bg-gray-100 flex items-center justify-center overflow-hidden mx-auto">
        {url && isVideo ? (
          <video
            src={url}
            poster={poster || undefined}
            controls
            muted
            playsInline
            preload="metadata"
            className="w-full h-full object-cover"
          />
        ) : url ? (
          <img src={url} alt="" className="w-full h-full object-cover" referrerPolicy="no-referrer" />
        ) : (
          <div className="text-gray-300 text-sm">нет медиа</div>
        )}
      </div>

      <div className="p-4 flex-1 flex flex-col">
        <div className="flex items-center justify-between mb-2 text-sm">
          <span className="font-medium truncate flex items-center gap-2">
            {ad.page_name || '—'}
            {ad.duplicates_count > 0 ? (
              <span
                title="Это медиа встречается ещё в N объявлениях"
                className="text-[10px] bg-orange-100 text-orange-700 px-1.5 py-0.5 rounded-full font-normal"
              >
                +{ad.duplicates_count}
              </span>
            ) : null}
          </span>
          <span className={ad.is_active ? 'text-green-600 text-xs' : 'text-gray-400 text-xs'}>
            {ad.is_active ? 'Active' : 'Inactive'}
          </span>
        </div>

        <div className="text-xs text-gray-500 mb-2">
          {ad.country} · {ad.keyword} · ID {ad.library_id}
        </div>

        <div className="text-sm text-gray-700 line-clamp-4 mb-3 whitespace-pre-line">
          {ad.body || '—'}
        </div>

        {ad.link_url ? (
          <button
            type="button"
            onClick={openLink}
            className="text-left text-blue-600 text-xs truncate hover:underline mb-3 bg-transparent border-0 p-0 cursor-pointer"
          >
            {ad.display_url || ad.link_url}
          </button>
        ) : null}

        {showActions ? (
          <div className="flex gap-2 mt-auto pt-2">
            <button
              onClick={onApprove}
              className="flex-1 bg-green-600 text-white py-2 rounded-lg hover:bg-green-700 text-sm"
            >
              ✓ Принять
            </button>
            <button
              onClick={onReject}
              className="flex-1 bg-red-100 text-red-700 py-2 rounded-lg hover:bg-red-200 text-sm"
            >
              ✕ В мусор
            </button>
          </div>
        ) : item.reject_reason ? (
          <div className="mt-auto pt-2 text-xs text-red-500">
            Причина: {item.reject_reason}
          </div>
        ) : null}
      </div>
    </div>
  )
}
