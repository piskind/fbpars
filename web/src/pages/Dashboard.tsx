import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'

type Stats = {
  total_ads: number
  active_ads: number
  pending: number
  approved: number
  rejected: number
  active_configs: number
  by_geo: { country: string; count: number }[]
  by_keyword: { keyword: string; count: number }[]
}

function StatCard({ label, value, sub }: { label: string; value: number; sub?: string }) {
  return (
    <div className="bg-white rounded-xl shadow p-5">
      <div className="text-3xl font-bold">{value.toLocaleString()}</div>
      <div className="text-sm text-gray-500 mt-1">{label}</div>
      {sub && <div className="text-xs text-gray-400 mt-0.5">{sub}</div>}
    </div>
  )
}

export function DashboardPage() {
  const { data, isLoading } = useQuery<Stats>({
    queryKey: ['stats'],
    queryFn: async () => (await api.get('/stats')).data,
    refetchInterval: 60_000,
  })

  if (isLoading) return <div className="text-gray-400">Загрузка...</div>
  if (!data) return null

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">Главная</h1>

      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4 mb-8">
        <StatCard label="Всего крео" value={data.total_ads} />
        <StatCard label="Активных" value={data.active_ads} />
        <StatCard label="На проверке" value={data.pending} />
        <StatCard label="Одобрено" value={data.approved} />
        <StatCard label="Отклонено" value={data.rejected} />
        <StatCard label="Конфигов" value={data.active_configs} sub="активных" />
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <div className="bg-white rounded-xl shadow p-5">
          <h2 className="font-semibold mb-4 text-gray-700">По гео</h2>
          <table className="w-full text-sm">
            <tbody>
              {data.by_geo.map((row) => (
                <tr key={row.country} className="border-b last:border-0">
                  <td className="py-2 font-mono font-medium">{row.country}</td>
                  <td className="py-2 text-right text-gray-500">{row.count.toLocaleString()}</td>
                  <td className="py-2 pl-3 w-32">
                    <div
                      className="h-2 bg-blue-200 rounded"
                      style={{
                        width: `${Math.round((row.count / data.total_ads) * 100)}%`,
                        minWidth: 4,
                      }}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="bg-white rounded-xl shadow p-5">
          <h2 className="font-semibold mb-4 text-gray-700">По ключевым словам</h2>
          <table className="w-full text-sm">
            <tbody>
              {data.by_keyword.map((row) => (
                <tr key={row.keyword} className="border-b last:border-0">
                  <td className="py-2 font-medium">{row.keyword}</td>
                  <td className="py-2 text-right text-gray-500">{row.count.toLocaleString()}</td>
                  <td className="py-2 pl-3 w-32">
                    <div
                      className="h-2 bg-green-200 rounded"
                      style={{
                        width: `${Math.round((row.count / data.total_ads) * 100)}%`,
                        minWidth: 4,
                      }}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
