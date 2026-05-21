import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useClientAuth } from '../../clientAuth/store'

export function ClientSignupPage() {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [verifyUrl, setVerifyUrl] = useState<string | null>(null)
  const { signUp, loading, error } = useClientAuth()
  const nav = useNavigate()

  const onSubmit = async () => {
    const res = await signUp(email, password)
    if (res.ok && res.verifyUrl) {
      setVerifyUrl(res.verifyUrl)
    }
  }

  if (verifyUrl) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50">
        <div className="bg-white rounded-xl shadow p-6 max-w-md">
          <h1 className="text-xl font-bold mb-3">Подтвердите email</h1>
          <p className="text-sm text-gray-600 mb-4">
            Пока сервис в тестовом режиме, ссылка для подтверждения здесь:
          </p>
          <a
            className="block text-blue-600 break-all text-sm hover:underline mb-4"
            href={verifyUrl}
            target="_blank"
            rel="noopener noreferrer"
          >
            {verifyUrl}
          </a>
          <button
            onClick={() => nav('/login')}
            className="w-full bg-blue-600 text-white py-2 rounded-lg hover:bg-blue-700"
          >
            Перейти к логину
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50">
      <div className="bg-white rounded-xl shadow p-6 w-96">
        <h1 className="text-xl font-bold mb-4">Регистрация</h1>
        <input
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="email"
          type="email"
          className="w-full px-3 py-2 border rounded-lg mb-3"
        />
        <input
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="пароль (6+ символов)"
          type="password"
          className="w-full px-3 py-2 border rounded-lg mb-3"
        />
        {error ? <div className="text-red-500 text-sm mb-3">{error}</div> : null}
        <button
          onClick={onSubmit}
          disabled={loading || !email || password.length < 6}
          className="w-full bg-blue-600 text-white py-2 rounded-lg hover:bg-blue-700 disabled:opacity-50"
        >
          Создать аккаунт
        </button>
        <div className="text-sm text-gray-500 mt-4 text-center">
          Уже есть аккаунт? <Link to="/login" className="text-blue-600 hover:underline">Войти</Link>
        </div>
      </div>
    </div>
  )
}
