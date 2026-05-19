import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../api/client'
import type { Config } from '../api/client'

export function ConfigsPage() {
  const qc = useQueryClient()
  const { data, isLoading } = useQuery({
    queryKey: ['configs'],
    queryFn: async () => (await api.get<Config[]>('/configs')).data,
  })

  const [showForm, setShowForm] = useState(false)
  const [keyword, setKeyword] = useState('')
  const [country, setCountry] = useState('')

  const createMut = useMutation({
    mutationFn: async () => {
      await api.post('/configs', { keyword, country, is_active: true })
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['configs'] })
      setShowForm(false)
      setKeyword('')
      setCountry('')
    },
  })

  const toggleMut = useMutation({
    mutationFn: async (c: Config) => {
      await api.patch(`/configs/${c.id}`, {
        keyword: c.keyword,
        country: c.country,
        is_active: !c.is_active,
        notes: c.notes,
      })
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['configs'] }),
  })

  const deleteMut = useMutation({
    mutationFn: async (id: number) => {
      await api.delete(`/configs/${id}`)
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['configs'] }),
  })

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">Парсинг-конфиги</h1>
        <button
          onClick={() => setShowForm(!showForm)}
          className="bg-blue-600 text-white px-4 py-2 rounded-lg hover:bg-blue-700"
        >
          {showForm ? 'Отмена' : '+ Добавить'}
        </button>
      </div>

      {showForm && (
        <div className="bg-white p-4 rounded-xl shadow mb-6 flex gap-2 items-end">
          <div className="flex-1">
            <label className="block text-sm mb-1">Ключ</label>
            <input
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
              placeholder="OXYS"
              className="w-full px-3 py-2 border rounded-lg"
            />
          </div>
          <div className="w-24">
            <label className="block text-sm mb-1">Гео</label>
            <input
              value={country}
              onChange={(e) => setCountry(e.target.value.toUpperCase())}
              placeholder="PE"
              maxLength={2}
              className="w-full px-3 py-2 border rounded-lg"
            />
          </div>
          <button
            onClick={() => createMut.mutate()}
            disabled={!keyword || !country || createMut.isPending}
            className="bg-green-600 text-white px-4 py-2 rounded-lg hover:bg-green-700 disabled:opacity-50"
          >
            Сохранить
          </button>
        </div>
      )}

      {isLoading && <div>Загрузка...</div>}

      <div className="bg-white rounded-xl shadow overflow-hidden">
        <table className="w-full">
          <thead className="bg-gray-50 border-b">
            <tr>
              <th className="text-left px-4 py-3 text-sm">ID</th>
              <th className="text-left px-4 py-3 text-sm">Ключ</th>
              <th className="text-left px-4 py-3 text-sm">Гео</th>
              <th className="text-left px-4 py-3 text-sm">Активен</th>
              <th className="text-right px-4 py-3 text-sm">Действия</th>
            </tr>
          </thead>
          <tbody>
            {data?.map((c) => (
              <tr key={c.id} className="border-b last:border-b-0">
                <td className="px-4 py-3 text-sm text-gray-500">{c.id}</td>
                <td className="px-4 py-3 font-medium">{c.keyword}</td>
                <td className="px-4 py-3">{c.country}</td>
                <td className="px-4 py-3">
                  <span
                    className={
                      c.is_active
                        ? 'text-green-600'
                        : 'text-gray-400'
                    }
                  >
                    {c.is_active ? '✓ да' : '— нет'}
                  </span>
                </td>
                <td className="px-4 py-3 text-right">
                  <button
                    onClick={() => toggleMut.mutate(c)}
                    className="text-blue-600 hover:underline mr-3 text-sm"
                  >
                    {c.is_active ? 'Выключить' : 'Включить'}
                  </button>
                  <button
                    onClick={() => {
                      if (confirm(`Удалить ${c.keyword}/${c.country}?`)) {
                        deleteMut.mutate(c.id)
                      }
                    }}
                    className="text-red-600 hover:underline text-sm"
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