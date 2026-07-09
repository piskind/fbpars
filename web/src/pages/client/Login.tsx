import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import axios from 'axios'
import { useClientAuth } from '../../clientAuth/store'
import { clientApi } from '../../api/client'

export function ClientLoginPage() {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [verifyUrl, setVerifyUrl] = useState<string | null>(null)
  const [resending, setResending] = useState(false)
  const [resentOk, setResentOk] = useState(false)
  const [resendError, setResendError] = useState<string | null>(null)
  const { signIn, loading, error } = useClientAuth()
  const nav = useNavigate()

  const onSubmit = async () => {
    setVerifyUrl(null)
    const ok = await signIn(email, password)
    if (ok) nav('/feed')
  }

  const onResend = async () => {
    setResending(true)
    setResentOk(false)
    setResendError(null)
    try {
      // backend returns { ok: true, verification_url: string | null }
      // verification_url is non-null only when DEBUG=true
      const { data } = await clientApi.post('/client/resend-verification', { email, password })
      if (data.verification_url) {
        setVerifyUrl(data.verification_url)
      } else {
        setResentOk(true)
      }
    } catch (e: unknown) {
      const msg = axios.isAxiosError(e)
        ? (e.response?.data?.detail ?? e.message)
        : 'Ошибка отправки'
      setResendError(msg)
    } finally {
      setResending(false)
    }
  }

  const isNotVerified = error === 'Email not verified'

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50">
      <div className="bg-white rounded-xl shadow p-6 w-96">
        <h1 className="text-xl font-bold mb-4">Вход</h1>
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
          placeholder="пароль"
          type="password"
          className="w-full px-3 py-2 border rounded-lg mb-3"
        />
        {error ? <div className="text-red-500 text-sm mb-3">{error}</div> : null}
        {isNotVerified && (
          <div className="mb-3">
            {verifyUrl ? (
              <div className="bg-blue-50 border border-blue-200 rounded-lg p-3 text-sm">
                <p className="text-gray-700 mb-2">Перейдите по ссылке для подтверждения:</p>
                <a href={verifyUrl} className="text-blue-600 hover:underline break-all">
                  {verifyUrl}
                </a>
              </div>
            ) : resentOk ? (
              <div className="bg-green-50 border border-green-200 rounded-lg p-3 text-sm text-green-700">
                Письмо с подтверждением отправлено на <strong>{email}</strong>
              </div>
            ) : (
              <>
                <button
                  onClick={onResend}
                  disabled={resending || !email || !password}
                  className="w-full border border-blue-600 text-blue-600 py-2 rounded-lg hover:bg-blue-50 disabled:opacity-50 text-sm"
                >
                  {resending ? 'Отправляем...' : 'Получить ссылку подтверждения'}
                </button>
                {resendError && (
                  <div className="text-red-500 text-xs mt-1">{resendError}</div>
                )}
              </>
            )}
          </div>
        )}
        <button
          onClick={onSubmit}
          disabled={loading || !email || !password}
          className="w-full bg-blue-600 text-white py-2 rounded-lg hover:bg-blue-700 disabled:opacity-50"
        >
          Войти
        </button>
        <div className="text-sm text-gray-500 mt-4 text-center">
          Нет аккаунта? <Link to="/signup" className="text-blue-600 hover:underline">Зарегистрироваться</Link>
        </div>
      </div>
    </div>
  )
}