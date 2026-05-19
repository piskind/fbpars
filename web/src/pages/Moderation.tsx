import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import type { ModerationItem } from '../api/client'

export function ModerationPage() {
  return <ModerationList status="pending" title="Модерация" />
}

export function TrashPage() {
  return <ModerationList status="rejected" title="Мусор" />
}

function mediaUrl(s3Url: string | null): string | null {
  if (!s3Url) return null
  const m = s3Url.match(/\/(ads\/.+)$/)
  if (!m) return null
  return `/api/media/${m[1]}`
}

function ModerationList({ status, title }: { status: string; title: string }) {
  const qc = useQueryClient()

  const { data, isLoading } = useQuery({
    queryKey: ['moderation', status],
    queryFn: async () =>
      (await api.get<ModerationItem[]>(`/moderation?status=${status}&limit=100`))
        .data,
  })

  const review = useMutation({
    mutationFn: async ({
      id,
      newStatus,
      reason,
    }: {
      id: number
      newStatus: 'approved' | 'rejected'
      reason?: string
    }) => {
      await api.post(`/moderation/${id}`, {
        status: newStatus,
        reject_reason: reason,
      })
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['moderation'] })
    },
  })

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">
        {title}{' '}
        <span className="text-gray-400 text-base font-normal">
          ({data?.length ?? 0})
        </span>
      </h1>

      {isLoading && <div>Загрузка...</div>}

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {data?.map((item) => (
          <Card
            key={item.id}
            item={item}
            onApprove={() =>
              review.mutate({ id: item.id, newStatus: 'approved' })
            }
            onReject={() => {
              const reason =
                prompt('Причина отклонения (необязательно):') || undefined
              review.mutate({ id: item.id, newStatus: 'rejected', reason })
            }}
            showActions={status === 'pending'}
          />
        ))}
      </div>

      {!isLoading && data?.length === 0 && (
        <div className="text-gray-400 mt-12 text-center">Пусто</div>
      )}
    </div>
  )
}

function Card({
  item,
  onApprove,
  onReject,
  showActions,
}: {
  item: ModerationItem
  onApprove: () => void
  onReject: () => void
  showActions: boolean
}) {
  const { ad } = item
  const firstCreative = ad.creatives.find(c => c.s3_url) || ad.creatives[0]

  return (
    <div className="bg-white rounded-xl shadow overflow-hidden flex flex-col">
     <div className="aspect-square bg-gray-100 flex items-center justify-center overflow-hidden">
    {firstCreative && mediaUrl(firstCreative.s3_url) && firstCreative.media_type.toLowerCase() === 'video' ? (
    <video
      src={mediaUrl(firstCreative.s3_url)!}
      controls
      className="w-full h-full object-cover"
    />
  ) : firstCreative && mediaUrl(firstCreative.s3_url) ? (
    <img
      src={mediaUrl(firstCreative.s3_url)!}
      alt=""
      className="w-full h-full object-cover"
    />
  ) : (
    <div className="text-gray-300 text-sm">нет медиа</div>
  )}
</div>


      <div className="p-4 flex-1 flex flex-col">
        <div className="flex items-center justify-between mb-2 text-sm">
          <span className="font-medium truncate">{ad.page_name || '—'}</span>
          <span
            className={
              ad.is_active
                ? 'text-green-600 text-xs'
                : 'text-gray-400 text-xs'
            }
          >
            {ad.is_active ? 'Active' : 'Inactive'}
          </span>
        </div>

        <div className="text-xs text-gray-500 mb-2">
          {ad.country} · {ad.keyword} · ID {ad.library_id}
        </div>

        <div className="text-sm text-gray-700 line-clamp-4 mb-3 whitespace-pre-line">
          {ad.body || '—'}
        </div>

        {ad.link_url && (
          <a
          
            href={ad.link_url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-blue-600 text-xs truncate hover:underline mb-3"
          >
            {ad.display_url || ad.link_url}
          </a>
        )}

        {showActions && (
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
        )}

        {!showActions && item.reject_reason && (
          <div className="mt-auto pt-2 text-xs text-red-500">
            Причина: {item.reject_reason}
          </div>
        )}
      </div>
    </div>
  )
}