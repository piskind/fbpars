import { Link, NavLink, useNavigate } from 'react-router-dom'
import { ReactNode } from 'react'
import { useAuth } from '../auth/store'

export function Layout({ children }: { children: ReactNode }) {
  const { login, signOut } = useAuth()
  const navigate = useNavigate()

  const handleLogout = () => {
    signOut()
    navigate('/login')
  }

  const linkClass = ({ isActive }: { isActive: boolean }) =>
    `block px-4 py-2 rounded-lg transition ${
      isActive
        ? 'bg-blue-600 text-white'
        : 'text-gray-700 hover:bg-gray-100'
    }`

  return (
    <div className="flex h-full">
      <aside className="w-60 bg-white border-r border-gray-200 p-4 flex flex-col">
        <Link to="/" className="text-xl font-bold mb-6 px-2">
          FB Spy Admin
        </Link>
        <nav className="flex-1 space-y-1">
          <NavLink to="/" end className={linkClass}>
            Главная
          </NavLink>
          <NavLink to="/moderation" className={linkClass}>
            Модерация
          </NavLink>
          <NavLink to="/trash" className={linkClass}>
            Мусор
          </NavLink>
          <NavLink to="/configs" className={linkClass}>
            Парсинг
          </NavLink>
          <NavLink to="/users" className={linkClass}>
            Пользователи
          </NavLink>
        </nav>
        <div className="border-t pt-4 mt-4">
          <div className="text-sm text-gray-500 mb-2 px-2">
            {login || 'admin'}
          </div>
          <button
            onClick={handleLogout}
            className="w-full text-left px-4 py-2 text-red-600 hover:bg-red-50 rounded-lg transition"
          >
            Выйти
          </button>
        </div>
      </aside>
      <main className="flex-1 overflow-auto p-6">{children}</main>
    </div>
  )
}