import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { clientApi } from '../../api/client'
import type { Ad } from '../../api/client'

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

type Props = {
  adId: number | null
  onClose: () => void
}

export function AdDrawer({ adId, onClose }: Props) {
  const [expandedForId, setExpandedForId] = useState<number | null>(null)
  const expanded = expandedForId === adId
  const setExpanded = (v: boolean) => setExpandedForId(v ? adId : null)

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    if (adId) window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [adId, onClose])

  const { data: ad, isLoading } = useQuery({
    queryKey: ['feed-detail', adId],
    queryFn: async () => (await clientApi.get<Ad>(`/feed/${adId}`)).data,
    enabled: !!adId,
  })

  const { data: similar } = useQuery({
    queryKey: ['feed-similar', adId],
    queryFn: async () => (await clientApi.get<Ad[]>(`/feed/${adId}/similar`)).data,
    enabled: !!adId,
  })

  if (!adId) return null

  const pageFbUrl = ad ? adsLibraryUrl(ad.page_id) : null

  return (
    <>
      <div
        onClick={onClose}
        className="fixed inset-0 bg-black/30 z-40 lg:hidden"
      />
      <aside className="fixed top-0 right-0 h-full w-full lg:w-[360px] bg-white shadow-xl z-50 overflow-y-auto">
        <button
          onClick={onClose}
          className="absolute top-3 right-3 w-8 h-8 rounded-full bg-gray-100 hover:bg-gray-200 flex items-center justify-center text-gray-600 z-10"
          aria-label="Закрыть"
        >
          ✕
        </button>

        {isLoading || !ad ? (
          <div className="p-6 text-gray-400">Загрузка...</div>
        ) : (
          <div className="p-5 pt-12 space-y-4">
            <div className="flex items-start gap-3">
              <div className="w-10 h-10 rounded-full bg-gray-200 flex items-center justify-center text-gray-500 font-medium shrink-0">
                {(ad.page_name || '?').slice(0, 1).toUpperCase()}
              </div>
              <div className="flex-1 min-w-0">
                {pageFbUrl ? (
                  <a
                    href={pageFbUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="font-semibold text-sm hover:underline text-blue-600 break-words"
                  >
                    {ad.page_name || '—'}
                  </a>
                ) : (
                  <div className="font-semibold text-sm break-words">{ad.page_name || '—'}</div>
                )}
                <div className="text-xs text-gray-500 mt-0.5">
                  {ad.started_at
                    ? new Date(ad.started_at).toLocaleDateString('ru-RU')
                    : '—'}
                  {ad.duplicates_count > 0 ? (
                    <span className="ml-2 text-orange-600">+{ad.duplicates_count} дубл.</span>
                  ) : null}
                </div>
              </div>
              <span
                className={`shrink-0 text-xs px-2 py-1 rounded-full ${
                  ad.is_active ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-500'
                }`}
              >
                {ad.is_active ? 'Active' : 'Inactive'}
              </span>
            </div>

            <div className="space-y-3">
              {ad.creatives.map((c) => {
                const url = mediaUrl(c.s3_url)
                if (!url) return null
                const isVideo = c.media_type?.toLowerCase() === 'video'
                return (
                  <div key={c.id} className="bg-gray-100 rounded-lg overflow-hidden">
                    {isVideo ? (
                      <video src={url} controls className="w-full" preload="metadata" />
                    ) : (
                      <img src={url} alt="" className="w-full" />
                    )}
                    <div className="p-2 text-right">
                      <a
                        href={url}
                        download
                        className="text-blue-600 hover:underline text-xs"
                      >
                        ⬇ Скачать
                      </a>
                    </div>
                  </div>
                )
              })}
            </div>

            {ad.body ? (
              <div>
                <div
                  className={`text-sm whitespace-pre-line ${
                    expanded ? '' : 'line-clamp-3'
                  }`}
                >
                  {ad.body}
                </div>
                {ad.body.length > 150 ? (
                  <button
                    onClick={() => setExpanded(!expanded)}
                    className="text-xs text-blue-600 hover:underline mt-1"
                  >
                    {expanded ? 'Свернуть' : 'Показать полностью'}
                  </button>
                ) : null}
              </div>
            ) : null}

            {ad.cta_text ? (
              <div className="border-t pt-3">
                <div className="text-[11px] uppercase text-gray-400 mb-1">CTA</div>
                <div className="inline-block px-3 py-1.5 bg-blue-50 text-blue-700 text-xs rounded-full font-medium">
                  {ad.cta_text}
                </div>
              </div>
            ) : null}

            {ad.link_url ? (
              <div className="border-t pt-3">
                <div className="text-[11px] uppercase text-gray-400 mb-1">Ссылка</div>
                <a
                  href={ad.link_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-sm text-blue-600 hover:underline break-all"
                >
                  {ad.display_url || ad.link_url}
                </a>
              </div>
            ) : null}

            <div className="border-t pt-3 grid grid-cols-2 gap-y-2 text-xs">
              <div className="text-gray-400">Гео</div>
              <div className="text-gray-800">{ad.country}</div>

              <div className="text-gray-400">Ключ</div>
              <div className="text-gray-800">{ad.keyword || '—'}</div>

              <div className="text-gray-400">Вертикаль</div>
              <div className="text-gray-800">{ad.vertical || '—'}</div>

              <div className="text-gray-400">Library ID</div>
              <div className="text-gray-800 break-all">{ad.library_id}</div>

              <div className="text-gray-400">Дней активно</div>
              <div className="text-gray-800">{ad.days_active}</div>
            </div>

            {similar && similar.length > 0 ? (
              <div className="border-t pt-4">
                <div className="text-xs uppercase text-gray-400 mb-2">Похожие</div>
                <div className="grid grid-cols-3 gap-2">
                  {similar.map((s) => {
                    const cre = s.creatives.find((c) => c.s3_url) || s.creatives[0]
                    const url = cre ? mediaUrl(cre.s3_url) : null
                    const isVideo = cre?.media_type?.toLowerCase() === 'video'
                    return (
                      <div
                        key={s.id}
                        className="bg-gray-100 rounded-lg overflow-hidden aspect-square cursor-pointer hover:opacity-80 transition"
                        onClick={() => {
                          window.scrollTo({ top: 0, behavior: 'smooth' })
                          // переключение на другой ad через родительский колбэк
                          // используем хитрость: меняем URL hash и слушаем в parent? нет, проще:
                          // прокинем через events
                          const ev = new CustomEvent('drawer:select', { detail: s.id })
                          window.dispatchEvent(ev)
                        }}
                      >
                        {url && isVideo ? (
                          <video src={url} className="w-full h-full object-cover" muted />
                        ) : url ? (
                          <img src={url} alt="" className="w-full h-full object-cover" />
                        ) : null}
                      </div>
                    )
                  })}
                </div>
              </div>
            ) : null}
          </div>
        )}
      </aside>
    </>
  )
}