import { useEffect, useState } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import { clientApi } from '../../api/client'

export function ClientVerifyPage() {
  const { token } = useParams<{ token: string }>()
  const nav = useNavigate()
  const [status, setStatus] = useState<'loading' | 'ok' | 'error'>('loading')
  const [email, setEmail] = useState<string>('')
  useEffect(() => {
    if (!token) return
    clientApi
      .get(`/client/verify/${token}`)
      .then((res) => {
        setEmail(res.data.email)
        setStatus('ok')
      })
      .catch(() => setStatus('error'))
  }, [token])

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50">
      <div className="bg-white rounded-xl shadow p-6 max-w-md w-full">
        {status === 'loading' && <div>Проверяем токен...</div>}
        {status === 'ok' && (
          <>
            <h1 className="text-xl font-bold mb-3">Email подтверждён</h1>
            <p className="text-sm text-gray-600 mb-4">
              {email} активирован. Теперь можно войти.
            </p>
            <button
              onClick={() => nav('/login')}
              className="w-full bg-blue-600 text-white py-2 rounded-lg hover:bg-blue-700"
            >
              Войти
            </button>
          </>
        )}
        {status === 'error' && (
          <>
            <h1 className="text-xl font-bold mb-3">Ссылка недействительна</h1>
            <p className="text-sm text-gray-600 mb-4">
              Токен не найден или уже использован.
            </p>
            <Link to="/signup" className="text-blue-600 hover:underline text-sm">
              Зарегистрироваться заново
            </Link>
          </>
        )}
      </div>
    </div>
  )
}