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

function verticalLabel(v: string): string {
  return VERTICALS.find((x) => x.value === v)?.label || v
}

export default function ConfigsPage() {
  const qc = useQueryClient()
  const { data, isLoading } = useQuery({
    queryKey: ['configs'],
    queryFn: async () => (await api.get<Config[]>('/configs')).data,
  })

  const [keyword, setKeyword] = useState('')
  const [country, setCountry] = useState('')
  const [vertical, setVertical] = useState('nutra')
  const [notes, setNotes] = useState('')
  const [partner, setPartner] = useState('')
  const [category, setCategory] = useState('')

  const create = useMutation({
    mutationFn: async () => {
      await api.post('/configs', {
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

  const toggle = useMutation({
    mutationFn: async (c: Config) => {
      await api.patch(`/configs/${c.id}`, { is_active: !c.is_active })
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['configs'] })
    },
  })

  const remove = useMutation({
    mutationFn: async (id: number) => {
      await api.delete(`/configs/${id}`)
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['configs'] })
    },
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

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">Парсинг</h1>

      <div className="bg-white rounded-xl shadow p-4 mb-6 flex flex-wrap gap-3 items-end">
        <div>
          <label className="block text-xs text-gray-500 mb-1">Ключ</label>
          <input
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
            placeholder="OXYS"
            className="px-3 py-2 border rounded-lg text-sm"
          />
        </div>
        <div>
          <label className="block text-xs text-gray-500 mb-1">Гео</label>
          <input
            value={country}
            onChange={(e) => setCountry(e.target.value)}
            placeholder="MX"
            className="px-3 py-2 border rounded-lg text-sm w-24"
          />
        </div>
        <div>
          <label className="block text-xs text-gray-500 mb-1">Вертикаль</label>
          <select
            value={vertical}
            onChange={(e) => setVertical(e.target.value)}
            className="px-3 py-2 border rounded-lg text-sm"
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
            className="px-3 py-2 border rounded-lg text-sm"
          />
        </div>
        <div>
          <label className="block text-xs text-gray-500 mb-1">Категория</label>
          <input
            value={category}
            onChange={(e) => setCategory(e.target.value)}
            placeholder="необязательно"
            className="px-3 py-2 border rounded-lg text-sm"
          />
        </div>
        <div className="flex-1 min-w-[200px]">
          <label className="block text-xs text-gray-500 mb-1">Заметка</label>
          <input
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="необязательно"
            className="w-full px-3 py-2 border rounded-lg text-sm"
          />
        </div>
        <button
          onClick={() => create.mutate()}
          disabled={!keyword || !country || create.isPending}
          className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700 disabled:opacity-50"
        >
          + Добавить
        </button>
      </div>

      {isLoading && <div>Загрузка...</div>}

      <div className="bg-white rounded-xl shadow overflow-hidden">
        <table className="w-full">
          <thead className="bg-gray-50 border-b">
            <tr>
              <th className="text-left px-4 py-3 text-sm">ID</th>
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
            {data?.map((c) => (
              <tr key={c.id} className="border-b last:border-0 hover:bg-gray-50">
                <td className="px-4 py-3 text-sm text-gray-500">{c.id}</td>
                <td className="px-4 py-3 font-medium">{c.keyword}</td>
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
