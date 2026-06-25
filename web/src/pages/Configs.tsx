import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../api/client'
import type { Config } from '../api/client'

const VERTICALS = [
  { value: 'nutra', label: 'Нутра' },
  { value: 'gambling', label: 'Гембла' },
  { value: 'dating', label: 'Дейтинг' },
  { value: 'crypto', label: 'Крипта' },
  { value: 'finance', label: 'Финансы' },
  { value: 'adult', label: 'Адалт' },
  { value: 'other', label: 'Прочее' },
]

const PLATFORMS = ['facebook', 'instagram', 'messenger', 'audience_network']

function verticalLabel(v: string): string {
  return VERTICALS.find((x) => x.value === v)?.label || v
}

function configTypeLabel(t: string): string {
  if (t === 'keyword') return 'Ключ'
  if (t === 'filters') return 'Фильтры'
  if (t === 'fanpage') return 'Фанпейдж'
  return t
}

type ActiveTab = 'keyword' | 'filters' | 'fanpage'

export default function ConfigsPage() {
  const qc = useQueryClient()
  const [activeTab, setActiveTab] = useState<ActiveTab>('keyword')

  const { data, isLoading } = useQuery({
    queryKey: ['configs'],
    queryFn: async () => (await api.get<Config[]>('/configs')).data,
  })

  // --- Keyword form state ---
  const [keyword, setKeyword] = useState('')
  const [country, setCountry] = useState('')
  const [vertical, setVertical] = useState('nutra')
  const [notes, setNotes] = useState('')
  const [partner, setPartner] = useState('')
  const [category, setCategory] = useState('')

  const createKeyword = useMutation({
    mutationFn: async () => {
      await api.post('/configs', {
        config_type: 'keyword',
        keyword,
        country,
        vertical,
        notes: notes || null,
        partner: partner || null,
        category: category || null,
        is_active: true,
      })
    },
    onSuccess: () => {
      setKeyword('')
      setCountry('')
      setVertical('nutra')
      setNotes('')
      setPartner('')
      setCategory('')
      qc.invalidateQueries({ queryKey: ['configs'] })
    },
  })

  // --- Filters form state ---
  const [fCountry, setFCountry] = useState('')
  const [fCategory, setFCategory] = useState('')
  const [fKeyword, setFKeyword] = useState('')
  const [fVertical, setFVertical] = useState('nutra')
  const [fLanguages, setFLanguages] = useState('')
  const [fAdvertiser, setFAdvertiser] = useState('')
  const [fPlatforms, setFPlatforms] = useState<string[]>([])
  const [fMediaType, setFMediaType] = useState('all')
  const [fActiveStatus, setFActiveStatus] = useState('all')
  const [fDateFrom, setFDateFrom] = useState('')
  const [fDateTo, setFDateTo] = useState('')
  const [fAutoDate, setFAutoDate] = useState(false)
  const [filtersOpen, setFiltersOpen] = useState(false)

  const createFilters = useMutation({
    mutationFn: async () => {
      const payload: Record<string, unknown> = {
        config_type: 'filters',
        country: fCountry,
        vertical: fVertical,
        keyword: fKeyword.trim() || null,
        category: fCategory || null,
        is_active: true,
      }
      if (fLanguages.trim()) {
        payload.languages = fLanguages.split(',').map((s) => s.trim()).filter(Boolean)
      }
      if (fAdvertiser.trim()) payload.advertiser = fAdvertiser.trim()
      if (fPlatforms.length > 0) payload.platforms = fPlatforms
      if (fMediaType !== 'all') payload.media_type_filter = fMediaType
      if (fActiveStatus !== 'all') payload.active_status = fActiveStatus
      if (fDateTo) payload.date_to = fDateTo
      if (fAutoDate) {
        payload.auto_date_from_last_parse = true
      } else if (fDateFrom) {
        payload.date_from = fDateFrom
      }
      await api.post('/configs', payload)
    },
    onSuccess: () => {
      setFCountry('')
      setFCategory('')
      setFKeyword('')
      setFVertical('nutra')
      setFLanguages('')
      setFAdvertiser('')
      setFPlatforms([])
      setFMediaType('all')
      setFActiveStatus('all')
      setFDateFrom('')
      setFDateTo('')
      setFAutoDate(false)
      qc.invalidateQueries({ queryKey: ['configs'] })
    },
  })

  const toggle = useMutation({
    mutationFn: async (c: Config) => {
      await api.patch(`/configs/${c.id}`, { is_active: !c.is_active })
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['configs'] }),
  })

  const remove = useMutation({
    mutationFn: async (id: number) => {
      await api.delete(`/configs/${id}`)
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['configs'] }),
  })

  const fmt = (s: string | null) => {
    if (!s) return '—'
    return new Date(s).toLocaleString('ru-RU', {
      day: '2-digit',
      month: '2-digit',
      year: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    })
  }

  const togglePlatform = (p: string) => {
    setFPlatforms((prev) =>
      prev.includes(p) ? prev.filter((x) => x !== p) : [...prev, p]
    )
  }

  const tabClass = (tab: ActiveTab) =>
    `px-4 py-2 rounded-full text-sm font-medium transition-colors ${
      activeTab === tab
        ? 'bg-blue-600 text-white'
        : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
    }`

  const inputClass = 'px-3 py-2 border rounded-lg text-sm'

  const tableData = activeTab === 'fanpage'
    ? data
    : data?.filter((c) => c.config_type === activeTab || !c.config_type)

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">Парсинг</h1>

      {/* Tabs */}
      <div className="flex gap-2 mb-6">
        <button className={tabClass('keyword')} onClick={() => setActiveTab('keyword')}>
          По ключу
        </button>
        <button className={tabClass('filters')} onClick={() => setActiveTab('filters')}>
          По фильтрам
        </button>
        <button
          disabled
          className="px-4 py-2 rounded-full text-sm font-medium bg-gray-100 text-gray-400 cursor-not-allowed"
          title="в разработке"
        >
          По фанпейдж
        </button>
      </div>

      {/* Tab 1: По ключу */}
      {activeTab === 'keyword' && (
        <div className="bg-white rounded-xl shadow p-4 mb-6 flex flex-wrap gap-3 items-end">
          <div>
            <label className="block text-xs text-gray-500 mb-1">Ключ</label>
            <input
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
              placeholder="OXYS"
              className={inputClass}
            />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">Гео</label>
            <input
              value={country}
              onChange={(e) => setCountry(e.target.value)}
              placeholder="MX"
              className={`${inputClass} w-24`}
            />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">Вертикаль</label>
            <select
              value={vertical}
              onChange={(e) => setVertical(e.target.value)}
              className={inputClass}
            >
              {VERTICALS.map((v) => (
                <option key={v.value} value={v.value}>{v.label}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">Партнёр</label>
            <input
              value={partner}
              onChange={(e) => setPartner(e.target.value)}
              placeholder="необязательно"
              className={inputClass}
            />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">Категория</label>
            <input
              value={category}
              onChange={(e) => setCategory(e.target.value)}
              placeholder="необязательно"
              className={inputClass}
            />
          </div>
          <div className="flex-1 min-w-[200px]">
            <label className="block text-xs text-gray-500 mb-1">Заметка</label>
            <input
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="необязательно"
              className={`w-full ${inputClass}`}
            />
          </div>
          <button
            onClick={() => createKeyword.mutate()}
            disabled={!keyword || !country || createKeyword.isPending}
            className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700 disabled:opacity-50"
          >
            + Добавить
          </button>
        </div>
      )}

      {/* Tab 2: По фильтрам */}
      {activeTab === 'filters' && (
        <div className="bg-white rounded-xl shadow p-4 mb-6">
          {/* Main fields row */}
          <div className="flex flex-wrap gap-3 items-end mb-4">
            <div>
              <label className="block text-xs text-gray-500 mb-1">Страна *</label>
              <input
                value={fCountry}
                onChange={(e) => setFCountry(e.target.value)}
                placeholder="MX"
                className={`${inputClass} w-24`}
              />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">Категория</label>
              <input
                value={fCategory}
                onChange={(e) => setFCategory(e.target.value)}
                placeholder="необязательно"
                className={inputClass}
              />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">Ключ</label>
              <input
                value={fKeyword}
                onChange={(e) => setFKeyword(e.target.value)}
                placeholder="невидимый пробел по умолчанию"
                className={`${inputClass} w-56`}
              />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">Вертикаль</label>
              <select
                value={fVertical}
                onChange={(e) => setFVertical(e.target.value)}
                className={inputClass}
              >
                {VERTICALS.map((v) => (
                  <option key={v.value} value={v.value}>{v.label}</option>
                ))}
              </select>
            </div>
          </div>

          {/* Collapsible filters section */}
          <div className="border rounded-lg overflow-hidden">
            <button
              type="button"
              onClick={() => setFiltersOpen((o) => !o)}
              className="w-full flex items-center justify-between px-4 py-2 bg-gray-50 text-sm font-medium text-gray-700 hover:bg-gray-100 transition-colors"
            >
              <span>Фильтры</span>
              <span className="text-gray-400">{filtersOpen ? '▲' : '▼'}</span>
            </button>
            {filtersOpen && (
              <div className="p-4 flex flex-wrap gap-4">
                <div>
                  <label className="block text-xs text-gray-500 mb-1">Язык (коды через запятую)</label>
                  <input
                    value={fLanguages}
                    onChange={(e) => setFLanguages(e.target.value)}
                    placeholder="es, en, ru"
                    className={`${inputClass} w-40`}
                  />
                </div>
                <div>
                  <label className="block text-xs text-gray-500 mb-1">Рекламодатель</label>
                  <input
                    value={fAdvertiser}
                    onChange={(e) => setFAdvertiser(e.target.value)}
                    placeholder="необязательно"
                    className={inputClass}
                  />
                </div>
                <div>
                  <label className="block text-xs text-gray-500 mb-1">Платформа</label>
                  <div className="flex flex-wrap gap-2 mt-1">
                    {PLATFORMS.map((p) => (
                      <label key={p} className="flex items-center gap-1 text-sm cursor-pointer">
                        <input
                          type="checkbox"
                          checked={fPlatforms.includes(p)}
                          onChange={() => togglePlatform(p)}
                          className="rounded"
                        />
                        {p}
                      </label>
                    ))}
                  </div>
                </div>
                <div>
                  <label className="block text-xs text-gray-500 mb-1">Тип медиа</label>
                  <select
                    value={fMediaType}
                    onChange={(e) => setFMediaType(e.target.value)}
                    className={inputClass}
                  >
                    <option value="all">Все</option>
                    <option value="image">Картинка</option>
                    <option value="video">Видео</option>
                    <option value="meme">Мем</option>
                  </select>
                </div>
                <div>
                  <label className="block text-xs text-gray-500 mb-1">Статус</label>
                  <select
                    value={fActiveStatus}
                    onChange={(e) => setFActiveStatus(e.target.value)}
                    className={inputClass}
                  >
                    <option value="all">Все и неактивные</option>
                    <option value="active">Активные</option>
                    <option value="inactive">Неактивные</option>
                  </select>
                </div>
                <div>
                  <label className="block text-xs text-gray-500 mb-1">Показы с</label>
                  <input
                    type="date"
                    value={fDateFrom}
                    onChange={(e) => setFDateFrom(e.target.value)}
                    disabled={fAutoDate}
                    className={`${inputClass} disabled:opacity-40`}
                  />
                </div>
                <div>
                  <label className="block text-xs text-gray-500 mb-1">Показы по</label>
                  <input
                    type="date"
                    value={fDateTo}
                    onChange={(e) => setFDateTo(e.target.value)}
                    className={inputClass}
                  />
                </div>
                <div className="flex items-end pb-1">
                  <label className="flex items-center gap-2 text-sm cursor-pointer">
                    <input
                      type="checkbox"
                      checked={fAutoDate}
                      onChange={(e) => setFAutoDate(e.target.checked)}
                      className="rounded"
                    />
                    с момента последнего парсинга
                  </label>
                </div>
              </div>
            )}
          </div>

          <div className="mt-4">
            <button
              onClick={() => createFilters.mutate()}
              disabled={!fCountry || createFilters.isPending}
              className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700 disabled:opacity-50"
            >
              + Добавить
            </button>
          </div>
        </div>
      )}

      {/* Tab 3: По фанпейдж — disabled stub */}
      {activeTab === 'fanpage' && (
        <div className="bg-white rounded-xl shadow p-6 mb-6 text-center text-gray-400 text-sm">
          в разработке
        </div>
      )}

      {isLoading && <div>Загрузка...</div>}

      <div className="bg-white rounded-xl shadow overflow-hidden">
        <table className="w-full">
          <thead className="bg-gray-50 border-b">
            <tr>
              <th className="text-left px-4 py-3 text-sm">ID</th>
              <th className="text-left px-4 py-3 text-sm">Тип</th>
              <th className="text-left px-4 py-3 text-sm">Ключ</th>
              <th className="text-left px-4 py-3 text-sm">Гео</th>
              <th className="text-left px-4 py-3 text-sm">Вертикаль</th>
              <th className="text-left px-4 py-3 text-sm">Партнёр</th>
              <th className="text-left px-4 py-3 text-sm">Категория</th>
              <th className="text-left px-4 py-3 text-sm">Спарсено</th>
              <th className="text-left px-4 py-3 text-sm">Последний парсинг</th>
              <th className="text-left px-4 py-3 text-sm">Активен</th>
              <th className="text-left px-4 py-3 text-sm">Заметка</th>
              <th className="text-right px-4 py-3 text-sm">Действия</th>
            </tr>
          </thead>
          <tbody>
            {tableData?.map((c) => (
              <tr key={c.id} className="border-b last:border-0 hover:bg-gray-50">
                <td className="px-4 py-3 text-sm text-gray-500">{c.id}</td>
                <td className="px-4 py-3 text-sm">
                  <span className="bg-gray-100 text-gray-600 px-2 py-0.5 rounded text-xs">
                    {configTypeLabel(c.config_type || 'keyword')}
                  </span>
                </td>
                <td className="px-4 py-3 font-medium">{c.keyword || '—'}</td>
                <td className="px-4 py-3">{c.country}</td>
                <td className="px-4 py-3 text-sm">
                  <span className="bg-purple-100 text-purple-700 px-2 py-0.5 rounded text-xs">
                    {verticalLabel(c.vertical)}
                  </span>
                </td>
                <td className="px-4 py-3 text-sm text-gray-500">{c.partner || '—'}</td>
                <td className="px-4 py-3 text-sm text-gray-500">{c.category || '—'}</td>
                <td className="px-4 py-3 text-sm">
                  {c.ads_count > 0 ? c.ads_count : <span className="text-gray-300">—</span>}
                </td>
                <td className="px-4 py-3 text-sm text-gray-500">{fmt(c.last_parsed_at)}</td>
                <td className="px-4 py-3">
                  <span className={c.is_active ? 'text-green-600' : 'text-gray-400'}>
                    {c.is_active ? '✓ да' : '— нет'}
                  </span>
                </td>
                <td className="px-4 py-3 text-sm text-gray-500">{c.notes || '—'}</td>
                <td className="px-4 py-3 text-right">
                  <button
                    onClick={() => toggle.mutate(c)}
                    className="text-blue-600 hover:underline text-sm mr-3"
                  >
                    {c.is_active ? 'Off' : 'On'}
                  </button>
                  <button
                    onClick={() => {
                      if (confirm('Удалить?')) remove.mutate(c.id)
                    }}
                    className="text-red-500 hover:underline text-sm"
                  >
                    Удалить
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
