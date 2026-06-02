import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'

type ParserRun = {
  id: number
  triggered_at: string
  started_at: string | null
  finished_at: string | null
  status: string
  stats: Record<string, number> | null
  log_tail: string | null
}

type ParserStatus = {
  running: boolean
  last_run: ParserRun | null
  recent_runs: ParserRun[]
}

function fmtDate(s: string | null): string {
  if (!s) return '—'
  return new Date(s).toLocaleString('ru-RU', {
    day: '2-digit', month: '2-digit', year: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  })
}

function fmtDuration(run: ParserRun): string {
  if (!run.started_at || !run.finished_at) return '—'
  const sec = Math.round((new Date(run.finished_at).getTime() - new Date(run.started_at).getTime()) / 1000)
  if (sec < 60) return `${sec}с`
  const min = Math.floor(sec / 60)
  return `${min}м ${sec % 60}с`
}

const STATUS_COLORS: Record<string, string> = {
  triggered: 'bg-yellow-100 text-yellow-700',
  running: 'bg-blue-100 text-blue-700',
  done: 'bg-green-100 text-green-700',
  failed: 'bg-red-100 text-red-700',
}

export default function ParserPage() {
  const qc = useQueryClient()

  const { data, isLoading } = useQuery({
    queryKey: ['parser-status'],
    queryFn: async () => (await api.get<ParserStatus>('/parser/status')).data,
    refetchInterval: (query) => {
      const d = query.state.data as ParserStatus | undefined
      return d?.running ? 5000 : 30000
    },
  })

  const { data: logsData } = useQuery({
    queryKey: ['parser-logs'],
    queryFn: async () => (await api.get<{ log_tail: string }>('/parser/logs')).data,
    refetchInterval: data?.running ? 5000 : false,
  })

  const start = useMutation({
    mutationFn: async () => (await api.post('/parser/start', {})).data,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['parser-status'] })
    },
  })

  const isRunning = data?.running ?? false

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">Парсер</h1>

      {/* Status card */}
      <div className="bg-white rounded-xl shadow p-5 mb-6 flex items-center gap-6">
        <div className="flex items-center gap-3">
          <span
            className={`w-3 h-3 rounded-full ${isRunning ? 'bg-green-500 animate-pulse' : 'bg-gray-300'}`}
          />
          <span className="font-medium text-lg">
            {isRunning ? 'Запущен' : 'Остановлен'}
          </span>
        </div>

        {data?.last_run && !isRunning && (
          <div className="text-sm text-gray-500">
            Последний запуск: {fmtDate(data.last_run.finished_at)} · {fmtDuration(data.last_run)}
          </div>
        )}

        <div className="ml-auto">
          <button
            onClick={() => start.mutate()}
            disabled={isRunning || start.isPending}
            className="px-5 py-2 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {isRunning ? '⏳ Работает...' : '▶ Запустить'}
          </button>
        </div>
      </div>

      {start.isError && (
        <div className="mb-4 px-4 py-2 bg-red-50 text-red-700 rounded-lg text-sm">
          {(() => {
            const e = start.error as { response?: { data?: { detail?: string } }; message?: string } | null
            return e?.response?.data?.detail ?? e?.message ?? 'Ошибка запуска'
          })()}
        </div>
      )}

      {isLoading && <div className="text-gray-400">Загрузка...</div>}

      {/* Recent runs table */}
      {(data?.recent_runs.length ?? 0) > 0 && (
        <div className="bg-white rounded-xl shadow overflow-hidden mb-6">
          <div className="px-5 py-3 border-b font-medium text-sm">Последние запуски</div>
          <table className="w-full text-sm">
            <thead className="bg-gray-50 border-b">
              <tr>
                <th className="text-left px-4 py-2">#</th>
                <th className="text-left px-4 py-2">Статус</th>
                <th className="text-left px-4 py-2">Начало</th>
                <th className="text-left px-4 py-2">Конец</th>
                <th className="text-left px-4 py-2">Длит.</th>
                <th className="text-left px-4 py-2">Новых</th>
                <th className="text-left px-4 py-2">Обновл.</th>
                <th className="text-left px-4 py-2">Медиа OK</th>
                <th className="text-left px-4 py-2">Ошибок</th>
                <th className="text-left px-4 py-2">Пропущено</th>
              </tr>
            </thead>
            <tbody>
              {data?.recent_runs.map((r) => (
                <tr key={r.id} className="border-b last:border-0 hover:bg-gray-50">
                  <td className="px-4 py-2 text-gray-400">{r.id}</td>
                  <td className="px-4 py-2">
                    <span className={`px-2 py-0.5 rounded text-xs font-medium ${STATUS_COLORS[r.status] ?? ''}`}>
                      {r.status}
                    </span>
                  </td>
                  <td className="px-4 py-2 text-gray-500">{fmtDate(r.started_at)}</td>
                  <td className="px-4 py-2 text-gray-500">{fmtDate(r.finished_at)}</td>
                  <td className="px-4 py-2">{fmtDuration(r)}</td>
                  <td className="px-4 py-2">{r.stats?.new ?? '—'}</td>
                  <td className="px-4 py-2">{r.stats?.updated ?? '—'}</td>
                  <td className="px-4 py-2">{r.stats?.media_ok ?? '—'}</td>
                  <td className="px-4 py-2 text-red-500">{r.stats?.errors ?? '—'}</td>
                  <td className="px-4 py-2 text-gray-400">{r.stats?.skipped_already_rejected ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Logs */}
      {logsData?.log_tail && (
        <div className="bg-white rounded-xl shadow overflow-hidden">
          <div className="px-5 py-3 border-b font-medium text-sm flex items-center gap-2">
            Логи
            {isRunning && <span className="w-2 h-2 rounded-full bg-green-500 animate-pulse" />}
          </div>
          <pre className="p-4 text-xs text-gray-600 overflow-auto max-h-96 bg-gray-50 font-mono whitespace-pre-wrap">
            {logsData.log_tail}
          </pre>
        </div>
      )}
    </div>
  )
}
