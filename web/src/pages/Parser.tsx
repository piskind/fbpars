import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'

type Mode = 'keyword' | 'filters' | 'all'

type ParserRun = {
  id: number
  triggered_at: string
  started_at: string | null
  finished_at: string | null
  status: string
  mode: string
  stats: Record<string, number> | null
  log_tail: string | null
}

type ParserStatus = {
  running: boolean
  current_mode: Mode | null
  last_run: ParserRun | null
  recent_runs: ParserRun[]
  active_keyword: number
  active_filters: number
}

const MODE_LABEL: Record<string, string> = {
  keyword: 'по ключам',
  filters: 'по фильтрам',
  all: 'всё вместе',
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
  if (sec < 3600) {
    const min = Math.floor(sec / 60)
    return `${min}м ${sec % 60}с`
  }
  const h = Math.floor(sec / 3600)
  const m = Math.floor((sec % 3600) / 60)
  return `${h}ч ${m}м`
}

const STATUS_COLORS: Record<string, string> = {
  triggered: 'bg-yellow-100 text-yellow-700',
  running: 'bg-blue-100 text-blue-700',
  done: 'bg-green-100 text-green-700',
  failed: 'bg-red-100 text-red-700',
  cancelled: 'bg-gray-100 text-gray-500',
}

function errText(e: unknown, fallback: string): string {
  const err = e as { response?: { data?: { detail?: string } }; message?: string } | null
  return err?.response?.data?.detail ?? err?.message ?? fallback
}

export default function ParserPage() {
  const qc = useQueryClient()
  const [notice, setNotice] = useState<string | null>(null)
  const [reloadOpen, setReloadOpen] = useState(false)

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
    mutationFn: async (mode: Mode) => (await api.post('/parser/start', { mode })).data,
    onSuccess: (_res, mode) => {
      setNotice(`Запуск «${MODE_LABEL[mode]}» — подхватится за ~15с`)
      qc.invalidateQueries({ queryKey: ['parser-status'] })
    },
  })

  const reload = useMutation({
    mutationFn: async (mode: Mode) => (await api.post('/parser/reload', { mode })).data,
    onSuccess: (_res, mode) => {
      setNotice(`Подхватываю новое (${MODE_LABEL[mode]}) — до ~10с`)
      qc.invalidateQueries({ queryKey: ['parser-status'] })
    },
  })

  const cancel = useMutation({
    mutationFn: async () => (await api.post('/parser/cancel', {})).data,
    onSuccess: () => {
      setNotice(null)
      qc.invalidateQueries({ queryKey: ['parser-status'] })
    },
  })

  const isRunning = data?.running ?? false
  const kw = data?.active_keyword ?? 0
  const fl = data?.active_filters ?? 0
  const busy = start.isPending || reload.isPending || cancel.isPending

  const startButtons: { mode: Mode; label: string; count: number; hint: string }[] = [
    { mode: 'keyword', label: 'По ключам', count: kw, hint: 'keyword-конфиги' },
    { mode: 'filters', label: 'По фильтрам', count: fl, hint: 'filters-конфиги' },
    { mode: 'all', label: 'Всё вместе', count: kw + fl, hint: 'все активные' },
  ]

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">Парсер</h1>

      {/* Status / control card */}
      <div className="bg-white rounded-xl shadow p-5 mb-6">
        <div className="flex items-center gap-4 mb-5">
          <div className="flex items-center gap-3">
            <span className={`w-3 h-3 rounded-full ${isRunning ? 'bg-green-500 animate-pulse' : 'bg-gray-300'}`} />
            <span className="font-medium text-lg">
              {isRunning ? 'Запущен' : 'Остановлен'}
            </span>
            {isRunning && data?.current_mode && (
              <span className="px-2 py-0.5 rounded text-xs font-medium bg-blue-100 text-blue-700">
                режим: {MODE_LABEL[data.current_mode] ?? data.current_mode}
              </span>
            )}
          </div>
          {data?.last_run && !isRunning && (
            <div className="text-sm text-gray-500">
              Последний: {fmtDate(data.last_run.finished_at)} · {fmtDuration(data.last_run)}
            </div>
          )}

          {/* Running: «подхватить новое» (dropdown) + cancel */}
          {isRunning && (
            <div className="ml-auto flex items-center gap-2">
              <div className="relative">
                <button
                  onClick={() => setReloadOpen((o) => !o)}
                  disabled={busy}
                  title="Добрать только что включённые конфиги, не останавливая сбор"
                  className="px-4 py-2 bg-emerald-100 text-emerald-700 rounded-lg text-sm hover:bg-emerald-200 disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-1"
                >
                  {reload.isPending ? '...' : '↻ Подхватить новое'}
                  <span className="text-[10px]">▼</span>
                </button>
                {reloadOpen && (
                  <>
                    <div className="fixed inset-0 z-10" onClick={() => setReloadOpen(false)} />
                    <div className="absolute right-0 mt-1 w-56 bg-white border border-gray-200 rounded-lg shadow-lg z-20 py-1">
                      {[
                        { m: 'keyword' as Mode, t: `Подхватить ключи (${kw})` },
                        { m: 'filters' as Mode, t: `Подхватить фильтры (${fl})` },
                        { m: 'all' as Mode, t: 'Подхватить всё' },
                      ].map((o) => (
                        <button
                          key={o.m}
                          onClick={() => { reload.mutate(o.m); setReloadOpen(false) }}
                          className="w-full text-left px-4 py-2 text-sm text-gray-700 hover:bg-emerald-50"
                        >
                          {o.t}
                        </button>
                      ))}
                    </div>
                  </>
                )}
              </div>
              <button
                onClick={() => cancel.mutate()}
                disabled={busy}
                className="px-4 py-2 bg-red-100 text-red-700 rounded-lg text-sm hover:bg-red-200 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {cancel.isPending ? '...' : '✕ Отменить'}
              </button>
            </div>
          )}
        </div>

        {/* Stopped: three launch buttons */}
        {!isRunning && (
          <div>
            <p className="text-sm text-gray-500 mb-3">
              Выбери, что запустить. Рестарт не нужен — сбор стартует автоматически за ~15с.
            </p>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
              {startButtons.map((b) => (
                <button
                  key={b.mode}
                  onClick={() => start.mutate(b.mode)}
                  disabled={busy || b.count === 0}
                  className="flex flex-col items-start gap-1 px-4 py-3 rounded-lg border border-gray-200 bg-white hover:border-blue-400 hover:bg-blue-50 disabled:opacity-40 disabled:cursor-not-allowed transition-colors text-left"
                >
                  <span className="font-medium text-gray-800">▶ {b.label}</span>
                  <span className="text-xs text-gray-500">{b.hint}</span>
                  <span className="mt-1 text-xs font-semibold text-blue-600">{b.count} конфигов</span>
                </button>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Notices / errors */}
      {notice && (
        <div className="mb-4 px-4 py-2 bg-blue-50 text-blue-700 rounded-lg text-sm flex items-center gap-2">
          <span>{notice}</span>
          <button onClick={() => setNotice(null)} className="ml-auto text-blue-400 hover:text-blue-600">✕</button>
        </div>
      )}
      {start.isError && (
        <div className="mb-4 px-4 py-2 bg-red-50 text-red-700 rounded-lg text-sm">
          {errText(start.error, 'Ошибка запуска')}
        </div>
      )}
      {reload.isError && (
        <div className="mb-4 px-4 py-2 bg-red-50 text-red-700 rounded-lg text-sm">
          {errText(reload.error, 'Ошибка подхвата')}
        </div>
      )}
      {cancel.isError && (
        <div className="mb-4 px-4 py-2 bg-red-50 text-red-700 rounded-lg text-sm">
          {errText(cancel.error, 'Ошибка отмены')}
        </div>
      )}

      {isLoading && <div className="text-gray-400">Загрузка...</div>}

      {/* Recent runs table */}
      {(data?.recent_runs.length ?? 0) > 0 && (
        <div className="bg-white rounded-xl shadow overflow-hidden mb-6">
          <div className="px-5 py-3 border-b font-medium text-sm">Последние запуски</div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 border-b">
                <tr>
                  <th className="text-left px-4 py-2">#</th>
                  <th className="text-left px-4 py-2">Режим</th>
                  <th className="text-left px-4 py-2">Статус</th>
                  <th className="text-left px-4 py-2">Начало</th>
                  <th className="text-left px-4 py-2">Конец</th>
                  <th className="text-left px-4 py-2">Длит.</th>
                  <th className="text-left px-4 py-2">Новых</th>
                  <th className="text-left px-4 py-2">Обновл.</th>
                  <th className="text-left px-4 py-2">С медиа</th>
                  <th className="text-left px-4 py-2">Ошибок</th>
                </tr>
              </thead>
              <tbody>
                {data?.recent_runs.map((r) => (
                  <tr key={r.id} className="border-b last:border-0 hover:bg-gray-50">
                    <td className="px-4 py-2 text-gray-400">{r.id}</td>
                    <td className="px-4 py-2 text-gray-600">{MODE_LABEL[r.mode] ?? r.mode ?? '—'}</td>
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
                    <td className="px-4 py-2">{r.stats?.urls_saved ?? r.stats?.media_ok ?? '—'}</td>
                    <td className="px-4 py-2 text-red-500">{r.stats?.errors ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
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
