import { useState, useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { clientApi } from '../../api/client'
import type { Ad } from '../../api/client'
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

type Props = {
  adId: number | null
  onClose: () => void
}

export function AdDrawer({ adId, onClose }: Props) {
  const [expandedForId, setExpandedForId] = useState<number | null>(null)
  const [similarBy, setSimilarBy] = useState<'fp' | 'domain'>('fp')
  const [prevAdId, setPrevAdId] = useState(adId)

  if (prevAdId !== adId) {
    setPrevAdId(adId)
    setSimilarBy('fp')
  }

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

  const { data: similar, isPending: similarPending, isError: similarError } = useQuery({
    queryKey: ['feed-similar', adId, similarBy],
    queryFn: async () => (await clientApi.get<Ad[]>(`/feed/${adId}/similar?by=${similarBy}`)).data,
    enabled: !!adId,
  })

  if (!adId) return null

  const pageFbUrl = ad ? adsLibraryUrl(ad.page_id) : null
  const expanded = expandedForId === adId

  return (
    <>
      <div onClick={onClose} className="fixed inset-0 bg-black/30 z-40 lg:hidden" />
      <aside className="fixed top-0 right-0 h-full w-full lg:w-[380px] bg-white shadow-xl z-50 overflow-y-auto">
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
            {/* Шапка */}
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
                <div className="text-xs text-gray-500 mt-0.5 flex items-center gap-2 flex-wrap">
                  <span>{countryFlag(ad.country)} {ad.country}</span>
                  <span>·</span>
                  <span>{ad.started_at ? new Date(ad.started_at).toLocaleDateString('ru-RU') : '—'}</span>
                  {ad.duplicates_count > 0 && (
                    <span className="text-orange-600">+{ad.duplicates_count} дубл.</span>
                  )}
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

            {/* Медиа */}
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
                      <a href={url} download className="text-blue-600 hover:underline text-xs">
                        ⬇ Скачать
                      </a>
                    </div>
                  </div>
                )
              })}
            </div>

            {/* Заголовок */}
            {ad.title && (
              <div className="border-t pt-3">
                <div className="text-[11px] uppercase text-gray-400 mb-1">Заголовок</div>
                <div className="text-sm font-medium">{ad.title}</div>
              </div>
            )}

            {/* Текст */}
            {ad.body && (
              <div className="border-t pt-3">
                <div className="text-[11px] uppercase text-gray-400 mb-1">Текст</div>
                <div className={`text-sm whitespace-pre-line ${expanded ? '' : 'line-clamp-3'}`}>
                  {ad.body}
                </div>
                {ad.body.length > 150 && (
                  <button
                    onClick={() => setExpandedForId(expanded ? null : adId)}
                    className="text-xs text-blue-600 hover:underline mt-1"
                  >
                    {expanded ? 'Свернуть' : 'Показать полностью'}
                  </button>
                )}
              </div>
            )}

            {/* Другой заголовок (caption) */}
            {ad.caption && (
              <div className="border-t pt-3">
                <div className="text-[11px] uppercase text-gray-400 mb-1">Подпись</div>
                <div className="text-sm">{ad.caption}</div>
              </div>
            )}

            {/* CTA */}
            {ad.cta_text && (
              <div className="border-t pt-3">
                <div className="text-[11px] uppercase text-gray-400 mb-1">CTA</div>
                <div className="inline-block px-3 py-1.5 bg-blue-50 text-blue-700 text-xs rounded-full font-medium">
                  {ad.cta_text}
                </div>
              </div>
            )}

            {/* Ссылка */}
            {ad.link_url && (
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
            )}

            {/* Плейсменты */}
            {ad.platforms && ad.platforms.length > 0 && (
              <div className="border-t pt-3">
                <div className="text-[11px] uppercase text-gray-400 mb-1">Плейсмент</div>
                <div className="flex flex-wrap gap-1.5">
                  {ad.platforms.map((p) => (
                    <span key={p} className="px-2 py-0.5 bg-gray-100 text-gray-700 text-xs rounded-full">
                      {p}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {/* Мета */}
            <div className="border-t pt-3 grid grid-cols-2 gap-y-2 text-xs">
              <div className="text-gray-400">Ключ</div>
              <div className="text-gray-800">{ad.keyword || '—'}</div>

              <div className="text-gray-400">Вертикаль</div>
              <div className="text-gray-800">{ad.vertical || '—'}</div>

              <div className="text-gray-400">Library ID</div>
              <div className="text-gray-800 break-all">{ad.library_id}</div>

              <div className="text-gray-400">Дней активно</div>
              <div className="text-gray-800">{ad.days_active}</div>

              <div className="text-gray-400">Последняя активность</div>
              <div className="text-gray-800">
                {new Date(ad.last_seen_at).toLocaleDateString('ru-RU')}
              </div>
              {ad.language && (
                <>
                  <div className="text-gray-400">Язык</div>
                  <div className="text-gray-800">{ad.language.toUpperCase()}</div>
                </>
              )}
              {ad.ip && (
                <>
                  <div className="text-gray-400">IP</div>
                  <div className="text-gray-800 font-mono text-[11px]">{ad.ip}</div>
                </>
              )}
              {ad.app_store && (
                <>
                  <div className="text-gray-400">Приложение</div>
                  <div className="text-gray-800">{ad.app_store}</div>
                </>
              )}
              {ad.ecom_platform && (
                <>
                  <div className="text-gray-400">E-com</div>
                  <div className="text-gray-800">{ad.ecom_platform}</div>
                </>
              )}
              {ad.lead_form && (
                <>
                  <div className="text-gray-400">Lead-form</div>
                  <div className="text-green-600 font-medium">Да</div>
                </>
              )}
            </div>

            {/* Похожие */}
            <div className="border-t pt-4">
              <div className="flex items-center justify-between mb-2">
                <div className="text-xs uppercase text-gray-400">Похожие</div>
                <div className="flex bg-gray-100 rounded-lg p-0.5 text-xs">
                  <button
                    onClick={() => setSimilarBy('fp')}
                    className={`px-2 py-0.5 rounded ${
                      similarBy === 'fp' ? 'bg-white shadow-sm' : 'text-gray-500'
                    }`}
                  >
                    По фанпейджу
                  </button>
                  <button
                    onClick={() => setSimilarBy('domain')}
                    className={`px-2 py-0.5 rounded ${
                      similarBy === 'domain' ? 'bg-white shadow-sm' : 'text-gray-500'
                    }`}
                  >
                    По домену
                  </button>
                </div>
              </div>
              {similarPending ? (
                <div className="text-xs text-gray-400">Загрузка...</div>
              ) : similarError ? (
                <div className="text-xs text-red-400">Ошибка загрузки</div>
              ) : similar && similar.length > 0 ? (
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
              ) : (
                <div className="text-xs text-gray-400">Нет похожих</div>
              )}
            </div>
          </div>
        )}
      </aside>
    </>
  )
}