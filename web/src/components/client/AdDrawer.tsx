import { useState, useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { clientApi, downloadMedia, mediaFilename, adMedia } from '../../api/client'
import type { Ad } from '../../api/client'
import { countryFlag } from '../../flags'
import { Copy, Check, ThumbsUp, Camera, MessageCircle, Globe, Phone } from 'lucide-react'

// Иконки плейсментов (значения publisher_platforms из FB GraphQL).
// lucide в этой версии не отдаёт бренд-иконки → берём узнаваемые generic-аналоги.
const PLATFORM_ICONS: Record<string, { Icon: typeof ThumbsUp; label: string }> = {
  facebook: { Icon: ThumbsUp, label: 'Facebook' },
  instagram: { Icon: Camera, label: 'Instagram' },
  messenger: { Icon: MessageCircle, label: 'Messenger' },
  whatsapp: { Icon: Phone, label: 'WhatsApp' },
  audience_network: { Icon: Globe, label: 'Audience Network' },
}

function PlatformBadge({ name }: { name: string }) {
  const key = name.toLowerCase().replace(/\s+/g, '_')
  const entry = PLATFORM_ICONS[key]
  if (entry) {
    const { Icon, label } = entry
    return (
      <span
        title={label}
        className="flex items-center gap-1 px-2 py-1 bg-gray-100 text-gray-700 text-xs rounded-full"
      >
        <Icon className="w-3.5 h-3.5" /> {label}
      </span>
    )
  }
  return <span className="px-2 py-0.5 bg-gray-100 text-gray-700 text-xs rounded-full">{name}</span>
}

function CopyButton({ text, className = '' }: { text: string; className?: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <button
      type="button"
      onClick={() => {
        navigator.clipboard.writeText(text).then(() => {
          setCopied(true)
          setTimeout(() => setCopied(false), 1500)
        })
      }}
      className={`relative text-gray-400 hover:text-gray-700 ${className}`}
      aria-label="Копировать"
    >
      {copied ? <Check className="w-3.5 h-3.5 text-green-600" /> : <Copy className="w-3.5 h-3.5" />}
      {copied && (
        <span className="absolute -top-6 right-0 bg-gray-800 text-white text-[10px] px-1.5 py-0.5 rounded whitespace-nowrap">
          Скопировано
        </span>
      )}
    </button>
  )
}

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

type DemoRow = { location: string; age: string; gender: string; reach: number }

function calcGenderText(rows: DemoRow[]): string | null {
  const totals: Record<string, number> = {}
  let total = 0
  for (const r of rows) {
    totals[r.gender] = (totals[r.gender] ?? 0) + r.reach
    total += r.reach
  }
  if (!total) return null
  const pct = (g: string) => Math.round(((totals[g] ?? 0) / total) * 100)
  const parts: string[] = []
  if (pct('Male') > 0) parts.push(`М ${pct('Male')}%`)
  if (pct('Female') > 0) parts.push(`Ж ${pct('Female')}%`)
  return parts.length ? parts.join(' · ') : null
}

function calcTopAge(rows: DemoRow[]): string | null {
  const totals: Record<string, number> = {}
  for (const r of rows) totals[r.age] = (totals[r.age] ?? 0) + r.reach
  const entries = Object.entries(totals).sort((a, b) => b[1] - a[1])
  return entries[0]?.[0] ?? null
}

type Props = {
  adId: number | null
  onClose: () => void
}

export function AdDrawer({ adId, onClose }: Props) {
  const [expandedForId, setExpandedForId] = useState<number | null>(null)
  const [similarBy, setSimilarBy] = useState<'fp' | 'domain' | 'ip'>('fp')
  const [prevAdId, setPrevAdId] = useState(adId)
  const [demoExpanded, setDemoExpanded] = useState(false)

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

  // Фанпейдж кликабелен, если есть прямой page_url, иначе строим ссылку по page_id.
  const pageFbUrl = ad ? (ad.page_url || adsLibraryUrl(ad.page_id)) : null
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
                  <span>{countryFlag(ad.country)} {ad.country?.toUpperCase()}</span>
                  {ad.duplicates_count > 0 && (
                    <span className="text-orange-600">+{ad.duplicates_count} дубл.</span>
                  )}
                </div>
              </div>
              <div className="shrink-0 flex flex-col items-end gap-1">
                <span
                  className={`text-xs px-2 py-1 rounded-full ${
                    ad.is_active ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-500'
                  }`}
                >
                  {ad.is_active ? 'Active' : 'Inactive'}
                </span>
                <span
                  className="text-[11px] text-gray-500"
                  title="дата создания объявления в FB"
                >
                  {ad.started_at ? new Date(ad.started_at).toLocaleDateString('ru-RU') : '—'}
                </span>
              </div>
            </div>

            {/* Медиа — прямые ссылки на FB CDN (без прохода через наш бэкенд/S3) */}
            <div className="space-y-3">
              {(() => {
                const vids = ad.video_urls ?? []
                const imgs = ad.image_urls ?? []
                const posters = ad.poster_urls ?? []
                if (vids.length > 0 || imgs.length > 0) {
                  return (
                    <>
                      {vids.map((v, i) => (
                        <div key={`v${i}`} className="bg-gray-100 rounded-lg overflow-hidden">
                          <video
                            src={v}
                            poster={posters[i] || undefined}
                            controls
                            className="w-full"
                            preload="metadata"
                          />
                          <div className="p-2 text-right">
                            <a
                              href={v}
                              download={mediaFilename(ad.library_id, 'video', v)}
                              target="_blank"
                              rel="noreferrer"
                              referrerPolicy="no-referrer"
                              className="text-blue-600 hover:underline text-xs"
                            >
                              ⬇ Скачать
                            </a>
                          </div>
                        </div>
                      ))}
                      {imgs.map((im, i) => (
                        <div key={`i${i}`} className="bg-gray-100 rounded-lg overflow-hidden">
                          <img src={im} alt="" className="w-full" referrerPolicy="no-referrer" />
                          <div className="p-2 text-right">
                            <a
                              href={im}
                              download={mediaFilename(ad.library_id, 'image', im)}
                              target="_blank"
                              rel="noreferrer"
                              referrerPolicy="no-referrer"
                              className="text-blue-600 hover:underline text-xs"
                            >
                              ⬇ Скачать
                            </a>
                          </div>
                        </div>
                      ))}
                    </>
                  )
                }
                // Legacy S3 fallback for ads parsed before the direct-media migration.
                return ad.creatives.map((c) => {
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
                        <button
                          type="button"
                          onClick={() => downloadMedia(url, mediaFilename(ad.library_id, c.media_type, url))}
                          className="text-blue-600 hover:underline text-xs"
                        >
                          ⬇ Скачать
                        </button>
                      </div>
                    </div>
                  )
                })
              })()}
            </div>

            {/* HD на FB */}
            {ad.library_id && (
              <div className="border-t pt-3">
                <button
                  onClick={() => window.open(`https://www.facebook.com/ads/library/?id=${ad.library_id}`, '_blank', 'noopener,noreferrer')}
                  className="px-3 py-1.5 bg-blue-600 text-white text-xs rounded-lg hover:bg-blue-700 font-medium"
                >
                  Открыть HD на FB
                </button>
                <div className="text-[11px] text-gray-400 mt-1">
                  для HD нужен аккаунт FB и расширение Ad Library Helper
                </div>
              </div>
            )}

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
                <div className="flex items-center justify-between mb-1">
                  <div className="text-[11px] uppercase text-gray-400">Текст</div>
                  <CopyButton text={ad.body} />
                </div>
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
            {(ad.link_url || ad.display_url) && (
              <div className="border-t pt-3">
                <div className="flex items-center justify-between mb-1">
                  <div className="text-[11px] uppercase text-gray-400">Ссылка</div>
                  <CopyButton text={ad.link_url || ad.display_url || ''} />
                </div>
                {ad.link_url ? (
                  <a
                    href={ad.link_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-sm text-blue-600 hover:underline break-all"
                  >
                    {ad.link_url}
                  </a>
                ) : (
                  <span className="text-sm text-gray-500 break-all">{ad.display_url}</span>
                )}
              </div>
            )}

            {/* Плейсменты */}
            {ad.platforms && ad.platforms.length > 0 && (
              <div className="border-t pt-3">
                <div className="text-[11px] uppercase text-gray-400 mb-1">Плейсмент</div>
                <div className="flex flex-wrap gap-1.5">
                  {ad.platforms.map((p) => (
                    <PlatformBadge key={p} name={p} />
                  ))}
                </div>
              </div>
            )}

            {/* Мета */}
            <div className="border-t pt-3 grid grid-cols-2 gap-y-2 text-xs">
              <div className="text-gray-400">Ключ</div>
              <div className="text-gray-800">{ad.keyword || '—'}</div>

              <div className="text-gray-400">Вертикаль</div>
              <div className="text-gray-800">
                {ad.vertical === 'general' ? 'Общее' : ad.vertical || '—'}
              </div>

              <div className="text-gray-400">Партнёрка</div>
              <div className={ad.partner ? 'font-medium text-indigo-700' : 'text-gray-800'}>
                {ad.partner || '—'}
              </div>

              <div className="text-gray-400">Library ID</div>
              <div className="text-gray-800 break-all">{ad.library_id}</div>

              <div className="text-gray-400">Фанпейдж</div>
              <div className="text-gray-800 break-all">
                {pageFbUrl ? (
                  <a
                    href={pageFbUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-blue-600 hover:underline"
                  >
                    {ad.page_name || 'Открыть страницу'}
                  </a>
                ) : (
                  '—'
                )}
              </div>

              <div className="text-gray-400">Дней активно</div>
              <div className="text-gray-800">{Math.max(1, ad.days_active)}</div>

              <div className="text-gray-400">Загружено</div>
              <div className="text-gray-800">
                {new Date(ad.first_seen_at).toLocaleDateString('ru-RU')}
              </div>

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

            {/* EU Статистика */}
            {(() => {
              const demo = (ad.reach_breakdown?.demographic ?? []) as DemoRow[]
              const genderText = demo.length ? calcGenderText(demo) : null
              const topAge = demo.length ? calcTopAge(demo) : null
              return (
                <div className="border-t pt-4">
                  <div className="text-xs uppercase text-gray-400 mb-2">Статистика по объявлению</div>
                  <div className="text-[11px] text-gray-400 bg-gray-50 rounded-md px-2 py-1 mb-3">
                    Доступно только для объявлений из стран ЕС
                  </div>
                  <div className="space-y-3">
                      {/* Три карточки: Охват / Пол / Возраст */}
                      <div className="grid grid-cols-3 gap-2">
                        <div className="bg-gray-50 rounded-lg p-2">
                          <div className="text-[10px] text-gray-400 mb-0.5">Охват</div>
                          <div className="text-sm font-semibold">
                            {ad.reach ? ad.reach.toLocaleString('ru-RU') : '0'}
                          </div>
                        </div>
                        <div className="bg-gray-50 rounded-lg p-2">
                          <div className="text-[10px] text-gray-400 mb-0.5">Пол</div>
                          <div className="text-xs font-medium leading-tight">{genderText || '--/--'}</div>
                        </div>
                        <div className="bg-gray-50 rounded-lg p-2">
                          <div className="text-[10px] text-gray-400 mb-0.5">Возраст</div>
                          <div className="text-xs font-medium">{topAge || '--/--'}</div>
                        </div>
                      </div>

                      {/* Дополнительные поля */}
                      <div className="grid grid-cols-2 gap-y-2 text-xs">
                        {ad.eu_countries && ad.eu_countries.length > 0 && (
                          <>
                            <div className="text-gray-400">Страны показа</div>
                            <div className="flex flex-wrap gap-1">
                              {ad.eu_countries.map((c) => (
                                <span key={c} className="px-1.5 py-0.5 bg-blue-50 text-blue-700 rounded text-[11px]">{c}</span>
                              ))}
                            </div>
                          </>
                        )}
                        {ad.spend_estimate != null && (
                          <>
                            <div className="text-gray-400">Спенд</div>
                            <div className="text-gray-800">
                              ≈ ${ad.spend_estimate.toLocaleString('ru-RU')}{' '}
                              <span className="text-gray-400">(оценка)</span>
                            </div>
                          </>
                        )}
                        {ad.used_in_ads_count != null && ad.used_in_ads_count > 1 && (
                          <>
                            <div className="text-gray-400">Использование</div>
                            <div className="text-gray-800">В {ad.used_in_ads_count} объявлениях</div>
                          </>
                        )}
                      </div>

                      {/* Сворачиваемая разбивка */}
                      {demo.length > 0 && (
                        <div>
                          <button
                            onClick={() => setDemoExpanded((v) => !v)}
                            className="text-xs text-blue-600 hover:underline flex items-center gap-1"
                          >
                            {demoExpanded ? '▲' : '▼'} Подробная разбивка
                          </button>
                          {demoExpanded && (
                            <div className="mt-2 overflow-x-auto">
                              <table className="w-full text-xs border-collapse">
                                <thead>
                                  <tr className="text-gray-400 border-b border-gray-200">
                                    <th className="text-left py-1 pr-2 font-normal">Страна</th>
                                    <th className="text-left py-1 pr-2 font-normal">Возраст</th>
                                    <th className="text-left py-1 pr-2 font-normal">Пол</th>
                                    <th className="text-right py-1 font-normal">Охват</th>
                                  </tr>
                                </thead>
                                <tbody>
                                  {demo.map((row, i) => (
                                    <tr key={i} className="border-b border-gray-100">
                                      <td className="py-1 pr-2 text-gray-700">{row.location}</td>
                                      <td className="py-1 pr-2 text-gray-700">{row.age}</td>
                                      <td className="py-1 pr-2 text-gray-700">
                                        {row.gender === 'Male' ? 'М' : row.gender === 'Female' ? 'Ж' : '—'}
                                      </td>
                                      <td className="py-1 text-right text-gray-800">{row.reach.toLocaleString('ru-RU')}</td>
                                    </tr>
                                  ))}
                                </tbody>
                              </table>
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                </div>
              )
            })()}

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
                  <button
                    onClick={() => setSimilarBy('ip')}
                    className={`px-2 py-0.5 rounded ${
                      similarBy === 'ip' ? 'bg-white shadow-sm' : 'text-gray-500'
                    }`}
                  >
                    По IP
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
                    const sm = adMedia(s)
                    const cre = s.creatives.find((c) => c.s3_url) || s.creatives[0]
                    const url = sm.url || (cre ? mediaUrl(cre.s3_url) : null)
                    const isVideo = sm.url ? sm.isVideo : cre?.media_type?.toLowerCase() === 'video'
                    return (
                      <div
                        key={s.id}
                        className="bg-gray-100 rounded-lg overflow-hidden aspect-square cursor-pointer hover:opacity-80 transition"
                        onClick={() => {
                          const ev = new CustomEvent('drawer:select', { detail: s.id })
                          window.dispatchEvent(ev)
                        }}
                      >
                        {sm.isVideo && sm.poster ? (
                          <img src={sm.poster} alt="" className="w-full h-full object-cover" referrerPolicy="no-referrer" />
                        ) : url && isVideo ? (
                          <video src={url} className="w-full h-full object-cover" muted playsInline preload="metadata" />
                        ) : url ? (
                          <img src={url} alt="" className="w-full h-full object-cover" referrerPolicy="no-referrer" />
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