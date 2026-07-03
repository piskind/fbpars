import type { ReactNode } from 'react'
import { NavLink, useNavigate } from 'react-router-dom'
import { useClientAuth } from '../../clientAuth/store'

export function ClientLayout({ children }: { children: ReactNode }) {
  const { user, signOut } = useClientAuth()
  const nav = useNavigate()

  const link = ({ isActive }: { isActive: boolean }) =>
    `block px-3 py-2 rounded-lg text-sm ${
      isActive ? 'bg-blue-600 text-white' : 'text-gray-700 hover:bg-gray-100'
    }`

  const stubs = ['Папки', 'Связки', 'Уникализация', 'Антикло', 'База знаний', 'Тарифы']

  return (
    <div className="min-h-screen bg-gray-50">
      <aside className="fixed top-0 left-0 w-56 h-screen bg-white border-r p-4 flex flex-col">
        <div className="mb-6 font-bold text-lg">FB Spy</div>
        <nav className="space-y-1 flex-1">
          <NavLink to="/" end className={link}>Главная</NavLink>
          <NavLink to="/feed" className={link}>Объявления</NavLink>
          {stubs.map((s) => (
            <div
              key={s}
              aria-disabled
              title="Скоро"
              className="block px-3 py-2 rounded-lg text-sm text-gray-700 opacity-50 cursor-not-allowed select-none"
            >
              {s}
            </div>
          ))}
        </nav>
        <div className="text-xs text-gray-500 mb-2 truncate">{user?.email}</div>
        <button
          onClick={() => {
            signOut()
            nav('/login')
          }}
          className="text-sm text-red-500 hover:underline text-left"
        >
          Выйти
        </button>
      </aside>
      <main className="ml-56 p-6">{children}</main>
    </div>
  )
}