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

type OtchetKonfig = {
  config_id: number
  keyword: string | null
  country: string
  tip: string
  setka: boolean
  srezov: number
  srezov_s_vydachey: number
  kartochek: number
  novyh: number
  sekund: number
  vsego_v_baze: number
  verdikt: string
  fb_schetchik: number | null
  fb_schetchik_at: string | null
}

type OtchetProgona = {
  run_id: number
  status: string
  mode: string
  nachat: string
  zakonchen: string
  srezov_v_plane: number | null
  srezov_sdelano: number | null
  vypolneno_procentov: number | null
  itogo: {
    konfigov: number
    srezov: number
    kartochek: number
    novyh: number
    pusto_u_fb: number
    podozritelnyh: number
  }
  konfigi: OtchetKonfig[]
}

type WordStats = {
  za_dney: number
  top_slova: { slovo: string; kreo: number; yazykov: number }[]
  slabye_slova: { slovo: string; kreo: number }[]
  po_yazykam: { yazyk: string; kreo: number; slov: number }[]
}

type SliceEvent = {
  kogda: string
  geo: string | null
  den: string | null
  slovo: string | null
  media: string | null
  status: string | null
  novyh: number
  vsego: number
  sekund: number
}

type LiveStats = {
  progon: { id: number; status: string; mode: string; nachat: string } | null
  zalito_za_chas: number
  zalito_za_sutki: number
  po_geo: { country: string; za_sutki: number }[]
  celi: {
    config_id: number
    country: string
    day: string
    vsego_v_baze: number
    za_etot_progon: number
    full: boolean
  }[]
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

  const { data: live } = useQuery({
    queryKey: ['parser-live'],
    queryFn: async () => (await api.get<LiveStats>('/parser/live')).data,
    refetchInterval: 30000,
  })

  const { data: words } = useQuery({
    queryKey: ['parser-words'],
    queryFn: async () => (await api.get<WordStats>('/parser/words')).data,
    refetchInterval: 120000,
  })

  const { data: slices } = useQuery({
    queryKey: ['parser-slices'],
    queryFn: async () => (await api.get<SliceEvent[]>('/parser/slices?limit=25')).data,
    refetchInterval: 5000,
  })

  // Сводка по прогону: какой конфиг что собрал и где ноль подозрительный.
  // По умолчанию — последний прогон.
  const [otchetRun, setOtchetRun] = useState<number | null>(null)
  // last_run в /status — это последний ЗАВЕРШЁННЫЙ прогон (done/failed), а отмена
  // для глубоких прогонов дело обычное. Без запасного варианта сводка просто
  // не показывалась: выбранного прогона нет — запрос выключен — блок пустой.
  const vybranyRun = otchetRun ?? data?.last_run?.id ?? data?.recent_runs?.[0]?.id ?? null
  const { data: otchet, isFetching: otchetIdet } = useQuery({
    queryKey: ['parser-otchet', vybranyRun],
    queryFn: async () =>
      (await api.get<OtchetProgona>(`/parser/otchet/${vybranyRun}`)).data,
    enabled: vybranyRun != null,
    refetchInterval: data?.running ? 15000 : false,
  })

  const [dayOpen, setDayOpen] = useState(false)
  const [dayCountries, setDayCountries] = useState('')
  const [dayDate, setDayDate] = useState('')
  const [dayDepth, setDayDepth] = useState<'quick' | 'full'>('quick')

  const startDay = useMutation({
    mutationFn: async () =>
      (await api.post('/parser/start-day', {
        countries: dayCountries.split(',').map((s) => s.trim()).filter(Boolean),
        day: dayDate || null,
        depth: dayDepth,
      })).data,
    onSuccess: (res: { strany: unknown; celevoy_den: string; glubina: string }) => {
      setDayOpen(false)
      setNotice(`Запущено: ${JSON.stringify(res.strany)} · ${res.celevoy_den} · ${res.glubina === 'full' ? 'полный' : 'проба'}`)
      qc.invalidateQueries({ queryKey: ['parser-status'] })
      qc.invalidateQueries({ queryKey: ['parser-live'] })
    },
    onError: (e) => setNotice(errText(e, 'Не удалось запустить')),
  })

  const start = useMutation({
    mutationFn: async (mode: Mode) => (await api.post('/parser/start', { mode })).data,
    onSuccess: (_res, mode) => {
      setNotice(`Запуск «${MODE_LABEL[mode]}» — подхватится за ~15с`)
      qc.invalidateQueries({ queryKey: ['parser-status'] })
    },
    // Без onError отказ сервера (например 409 «уже запущен») проходил молча —
    // кнопка выглядела нерабочей, хотя причина была понятной.
    onError: (e) => setNotice(errText(e, 'Не удалось запустить')),
  })

  const reload = useMutation({
    mutationFn: async (mode: Mode) => (await api.post('/parser/reload', { mode })).data,
    onSuccess: (_res, mode) => {
      setNotice(`Подхватываю новое (${MODE_LABEL[mode]}) — до ~10с`)
      qc.invalidateQueries({ queryKey: ['parser-status'] })
    },
    onError: (e) => setNotice(errText(e, 'Не удалось подхватить')),
  })

  const cancel = useMutation({
    mutationFn: async () => (await api.post('/parser/cancel', {})).data,
    onSuccess: () => {
      setNotice(null)
      qc.invalidateQueries({ queryKey: ['parser-status'] })
    },
    onError: (e) => setNotice(errText(e, 'Не удалось остановить')),
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
      <div className="flex items-center gap-3 mb-6">
        <h1 className="text-2xl font-bold">Парсер</h1>
        <button
          onClick={() => setDayOpen(true)}
          className="ml-auto px-4 py-2 rounded-lg bg-blue-600 text-white text-sm font-medium hover:bg-blue-700"
        >
          Запустить сбор…
        </button>
      </div>

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

      {/* Сводка по прогону: что собрано и что требует внимания */}
      {otchet && (
        <div className="bg-white rounded-xl shadow overflow-hidden mb-6">
          <div className="px-5 py-3 border-b flex items-center gap-3 flex-wrap">
            <span className="font-medium">Сводка по прогону #{otchet.run_id}</span>
            {/* Статус словами: «cancelled» читается как авария, хотя чаще это
                осознанная остановка на почти выполненном плане. */}
            <span className="text-xs text-gray-400">
              {otchet.status === 'cancelled'
                ? (otchet.vypolneno_procentov != null
                    ? `остановлен вручную · план выполнен на ${otchet.vypolneno_procentov}%`
                    : 'остановлен вручную')
                : otchet.status === 'done'
                  ? 'завершён полностью'
                  : otchet.status}
            </span>
            {otchet.srezov_v_plane != null && (
              <span className="text-xs text-gray-400">
                срезов {otchet.srezov_sdelano} из {otchet.srezov_v_plane}
              </span>
            )}
            {otchetIdet && <span className="text-xs text-gray-400">обновляю…</span>}
            <select
              className="ml-auto text-sm border rounded px-2 py-1"
              value={vybranyRun ?? ''}
              onChange={(e) => setOtchetRun(Number(e.target.value))}
            >
              {data?.recent_runs.map((r) => (
                <option key={r.id} value={r.id}>
                  #{r.id} · {MODE_LABEL[r.mode] ?? r.mode} · {r.status}
                </option>
              ))}
            </select>
          </div>

          <div className="px-5 py-3 flex flex-wrap gap-6 text-sm border-b bg-gray-50">
            <div><span className="text-gray-500">конфигов</span> <b>{otchet.itogo.konfigov}</b></div>
            <div><span className="text-gray-500">срезов</span> <b>{otchet.itogo.srezov}</b></div>
            <div><span className="text-gray-500">карточек от FB</span> <b>{otchet.itogo.kartochek.toLocaleString('ru')}</b></div>
            <div><span className="text-gray-500">новых</span> <b>{otchet.itogo.novyh.toLocaleString('ru')}</b></div>
            <div><span className="text-gray-500">пусто у FB</span> <b>{otchet.itogo.pusto_u_fb}</b></div>
            <div>
              <span className="text-gray-500">подозрительных нулей</span>{' '}
              <b className={otchet.itogo.podozritelnyh > 0 ? 'text-red-600' : 'text-green-600'}>
                {otchet.itogo.podozritelnyh}
              </b>
            </div>
          </div>

          <div className="px-5 py-2 text-xs text-gray-500 border-b">
            «Показывает FB» — счётчик со страницы Ad Library. Он считает совпадения
            по подстроке со стеммингом, а не объявления бренда: по «ProstaMen» это
            3 300 карточек про польское слово «prosta», настоящих объявлений бренда
            около полусотни. Сравнивать эту колонку с «В базе» напрямую нельзя.
          </div>

          {otchet.itogo.podozritelnyh > 0 && (
            <div className="px-5 py-2 text-sm bg-red-50 text-red-700 border-b">
              Ноль карточек при том, что объявления по ключу в базе есть — вероятно, FB
              придушил адрес. Такие срезы закладку не получают, следующий прогон возьмёт их заново.
            </div>
          )}

          <div className="max-h-96 overflow-auto">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 sticky top-0">
                <tr className="text-left text-gray-500 text-xs">
                  <th className="px-4 py-2">Ключ</th>
                  <th>Гео</th>
                  <th className="text-right">Срезов</th>
                  <th className="text-right">Карточек</th>
                  <th className="text-right">Новых</th>
                  <th className="text-right">В базе</th>
                  <th className="text-right" title="Сколько результатов показывает сам FB. Это совпадения по подстроке со стеммингом, а не объявления бренда: по «ProstaMen» FB даёт 3 300 — это польское слово «prosta».">
                    Показывает FB
                  </th>
                  <th className="px-3">Вердикт</th>
                </tr>
              </thead>
              <tbody>
                {otchet.konfigi.map((k) => {
                  const trevoga = k.verdikt === 'подозрительный ноль'
                  return (
                    <tr key={k.config_id} className={`border-b last:border-0 ${trevoga ? 'bg-red-50' : 'hover:bg-gray-50'}`}>
                      <td className="px-4 py-2">
                        {k.keyword || <span className="text-gray-400">по фильтрам</span>}
                        {k.setka && <span className="ml-2 text-xs text-blue-600">сетка</span>}
                      </td>
                      <td className="text-gray-600">{k.country}</td>
                      <td className="text-right">{k.srezov}</td>
                      <td className="text-right">{k.kartochek.toLocaleString('ru')}</td>
                      <td className="text-right">{k.novyh.toLocaleString('ru')}</td>
                      <td className="text-right text-gray-500">{k.vsego_v_baze.toLocaleString('ru')}</td>
                      <td className="text-right text-gray-400" title={k.fb_schetchik_at ? `снято ${k.fb_schetchik_at}` : 'ещё не снимали'}>
                        {k.fb_schetchik == null ? '—' : `~${k.fb_schetchik.toLocaleString('ru')}`}
                      </td>
                      <td className={`px-3 ${trevoga ? 'text-red-700 font-medium' : 'text-gray-500'}`}>
                        {k.verdikt}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Живой лог срезов */}
      {slices && slices.length > 0 && (
        <div className="bg-white rounded-xl shadow overflow-hidden mb-6">
          <div className="px-5 py-3 border-b flex items-center gap-2">
            <span className="font-medium">Срезы в реальном времени</span>
            <span className="w-2 h-2 rounded-full bg-green-500 animate-pulse" />
            <span className="text-xs text-gray-400 ml-auto">обновление каждые 5 с</span>
          </div>
          <div className="max-h-80 overflow-auto">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 sticky top-0">
                <tr className="text-left text-gray-500 text-xs">
                  <th className="px-4 py-2">Время</th>
                  <th>Гео</th>
                  <th>День</th>
                  <th>Слово</th>
                  <th>Медиа</th>
                  <th>Статус</th>
                  <th className="text-right">Новых</th>
                  <th className="text-right">Получено</th>
                  <th className="text-right pr-4">Сек</th>
                </tr>
              </thead>
              <tbody>
                {slices.map((s, i) => (
                  <tr key={i} className="border-t hover:bg-gray-50">
                    <td className="px-4 py-1.5 text-gray-400 text-xs whitespace-nowrap">
                      {new Date(s.kogda).toLocaleTimeString('ru', {
                        hour: '2-digit', minute: '2-digit', second: '2-digit',
                      })}
                    </td>
                    <td className="font-medium">{s.geo ?? '—'}</td>
                    <td className="text-gray-500">{s.den ?? '—'}</td>
                    <td className="font-mono text-xs">{s.slovo ?? '—'}</td>
                    <td className="text-gray-500 text-xs">{s.media ?? 'все'}</td>
                    <td className="text-gray-500 text-xs">{s.status ?? '—'}</td>
                    <td className={`text-right font-medium ${s.novyh > 0 ? 'text-green-600' : 'text-gray-300'}`}>
                      {s.novyh}
                    </td>
                    <td className="text-right text-gray-400">{s.vsego}</td>
                    <td className="text-right pr-4 text-gray-400">{s.sekund}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Живая картина сбора: считается по БД, а не по «ранам» */}
      {live && (
        <div className="bg-white rounded-xl shadow p-5 mb-6">
          <div className="font-medium mb-3">Что собирается сейчас</div>
          <div className="flex gap-6 mb-4 text-sm">
            <div>
              <span className="text-gray-500">залито за час:</span>{' '}
              <b>{live.zalito_za_chas.toLocaleString('ru')}</b>
            </div>
            <div>
              <span className="text-gray-500">за сутки:</span>{' '}
              <b>{live.zalito_za_sutki.toLocaleString('ru')}</b>
            </div>
          </div>

          {live.progon && (
            <div className="text-xs text-gray-500 mb-3">
              Последний прогон #{live.progon.id} · {live.progon.status} · запущен{' '}
              {fmtDate(live.progon.nachat)}
            </div>
          )}

          {live.celi.length > 0 && (
            <div className="mb-4">
              <div className="text-xs text-gray-500 mb-1">Сбор за конкретный день</div>
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-gray-500 text-xs">
                    <th className="py-1">Гео</th>
                    <th>Целевой день</th>
                    <th>За этот прогон</th>
                    <th>Всего в базе</th>
                    <th>Глубина</th>
                  </tr>
                </thead>
                <tbody>
                  {live.celi.map((c) => (
                    <tr key={c.config_id} className="border-t">
                      <td className="py-1 font-medium">{c.country}</td>
                      <td>{c.day}</td>
                      <td className="font-medium">{c.za_etot_progon.toLocaleString('ru')}</td>
                      <td className="text-gray-500">{c.vsego_v_baze.toLocaleString('ru')}</td>
                      <td className="text-gray-500">{c.full ? 'полный' : 'проба'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="text-xs text-gray-400 mt-1">
                «Всего в базе» — сколько объявлений этого дня и гео накоплено за всё время,
                включая прошлые прогоны. Смотреть на «за этот прогон».
              </div>
            </div>
          )}

          {live.po_geo.length > 0 && (
            <div>
              <div className="text-xs text-gray-500 mb-1">Залито за сутки по гео</div>
              <div className="flex flex-wrap gap-2">
                {live.po_geo.map((g) => (
                  <span key={g.country} className="px-2 py-0.5 rounded bg-gray-100 text-xs">
                    {g.country}: {g.za_sutki.toLocaleString('ru')}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Статистика по словам и языкам */}
      {words && (words.top_slova.length > 0 || words.po_yazykam.length > 0) && (
        <div className="bg-white rounded-xl shadow p-5 mb-6">
          <div className="font-medium mb-1">Слова и языки</div>
          <div className="text-xs text-gray-500 mb-3">
            За последние {words.za_dney} дней. Считается по слову, которым найдено
            объявление — раньше эта связь не сохранялась.
          </div>

          <div className="grid grid-cols-2 gap-6">
            <div>
              <div className="text-xs text-gray-500 mb-1">Языки</div>
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-gray-400 text-xs">
                    <th className="py-1">Язык</th><th>Крео</th><th>Слов</th>
                  </tr>
                </thead>
                <tbody>
                  {words.po_yazykam.slice(0, 12).map((l) => (
                    <tr key={l.yazyk} className="border-t">
                      <td className="py-1 font-medium">{l.yazyk}</td>
                      <td>{l.kreo.toLocaleString('ru')}</td>
                      <td className="text-gray-500">{l.slov}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div>
              <div className="text-xs text-gray-500 mb-1">Лучшие слова</div>
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-gray-400 text-xs">
                    <th className="py-1">Слово</th><th>Крео</th><th>Языков</th>
                  </tr>
                </thead>
                <tbody>
                  {words.top_slova.slice(0, 12).map((w) => (
                    <tr key={w.slovo} className="border-t">
                      <td className="py-1 font-medium">{w.slovo}</td>
                      <td>{w.kreo.toLocaleString('ru')}</td>
                      <td className="text-gray-500">{w.yazykov}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {words.slabye_slova.length > 0 && (
            <div className="mt-4">
              <div className="text-xs text-gray-500 mb-1">
                Слабые слова — кандидаты на выброс из словаря
              </div>
              <div className="flex flex-wrap gap-1">
                {words.slabye_slova.slice(0, 30).map((w) => (
                  <span key={w.slovo} className="px-2 py-0.5 rounded bg-gray-100 text-xs">
                    {w.slovo}: {w.kreo}
                  </span>
                ))}
              </div>
            </div>
          )}
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
      {dayOpen && (
        <div
          className="fixed inset-0 bg-black/40 flex items-center justify-center z-50"
          onClick={() => setDayOpen(false)}
        >
          <div
            className="bg-white rounded-xl shadow-xl p-6 w-[440px]"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="text-lg font-semibold mb-1">Запустить сбор</div>
            <div className="text-xs text-gray-500 mb-4">
              Для сбора за день отсечка выставляется автоматически на сутки позже —
              вручную считать не нужно.
            </div>

            <label className="block text-xs text-gray-500 mb-1">
              Гео: коды через запятую. Пусто — все активные конфиги
            </label>
            <input
              className="w-full border rounded px-3 py-2 mb-3 text-sm"
              placeholder="BR, MX, US"
              value={dayCountries}
              onChange={(e) => setDayCountries(e.target.value)}
            />

            <label className="block text-xs text-gray-500 mb-1">
              День запуска рекламы — один день, не диапазон. Для 5 мая укажите 05.05.2026.
              Пусто — весь период
            </label>
            <input
              type="date"
              className="w-full border rounded px-3 py-2 mb-3 text-sm"
              value={dayDate}
              onChange={(e) => setDayDate(e.target.value)}
            />

            <label className="block text-xs text-gray-500 mb-1">Глубина</label>
            <div className="flex gap-2 mb-4">
              <button
                onClick={() => setDayDepth('quick')}
                className={`px-3 py-2 rounded text-sm border ${
                  dayDepth === 'quick' ? 'bg-blue-50 border-blue-400 text-blue-700' : 'border-gray-200'
                }`}
              >
                Проба · ~15 мин
              </button>
              <button
                onClick={() => setDayDepth('full')}
                className={`px-3 py-2 rounded text-sm border ${
                  dayDepth === 'full' ? 'bg-blue-50 border-blue-400 text-blue-700' : 'border-gray-200'
                }`}
              >
                Полный · много часов
              </button>
            </div>

            <div className="flex gap-2 justify-end">
              <button
                onClick={() => setDayOpen(false)}
                className="px-3 py-2 text-sm text-gray-600"
              >
                Отмена
              </button>
              <button
                onClick={() => startDay.mutate()}
                disabled={startDay.isPending}
                className="px-4 py-2 text-sm rounded-lg bg-blue-600 text-white disabled:opacity-50"
              >
                {startDay.isPending ? 'Запускаю…' : 'Запустить'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
