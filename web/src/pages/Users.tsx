import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import type { ClientUser } from '../api/client'

export function UsersPage() {
  const { data, isLoading } = useQuery({
    queryKey: ['users'],
    queryFn: async () => (await api.get<ClientUser[]>('/users')).data,
  })

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">
        Пользователи{' '}
        <span className="text-gray-400 text-base font-normal">
          ({data?.length ?? 0})
        </span>
      </h1>

      {isLoading && <div>Загрузка...</div>}

      <div className="bg-white rounded-xl shadow overflow-hidden">
        <table className="w-full">
          <thead className="bg-gray-50 border-b">
            <tr>
              <th className="text-left px-4 py-3 text-sm">ID</th>
              <th className="text-left px-4 py-3 text-sm">Email</th>
              <th className="text-left px-4 py-3 text-sm">Подтверждён</th>
              <th className="text-left px-4 py-3 text-sm">Реферал</th>
              <th className="text-left px-4 py-3 text-sm">Регистрация</th>
            </tr>
          </thead>
          <tbody>
            {data?.map((u) => (
              <tr key={u.id} className="border-b last:border-b-0">
                <td className="px-4 py-3 text-sm text-gray-500">{u.id}</td>
                <td className="px-4 py-3">{u.email}</td>
                <td className="px-4 py-3 text-sm">
                  {u.email_verified ? '✓' : '—'}
                </td>
                <td className="px-4 py-3 text-sm text-gray-500">
                  {u.referral_source || '—'}
                </td>
                <td className="px-4 py-3 text-sm text-gray-500">
                  {new Date(u.created_at).toLocaleString('ru-RU')}
                </td>
              </tr>
            ))}
            {data?.length === 0 && (
              <tr>
                <td colSpan={5} className="px-4 py-8 text-center text-gray-400">
                  Пока нет зарегистрированных пользователей
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}