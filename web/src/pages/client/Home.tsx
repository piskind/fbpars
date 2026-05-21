export function ClientHomePage() {
  return (
    <div>
      <h1 className="text-2xl font-bold mb-4">Главная</h1>
      <div className="bg-white rounded-xl shadow p-6 text-gray-600">
        Добро пожаловать в FB Spy. Перейдите в раздел <a href="/feed" className="text-blue-600 hover:underline">Объявления</a>{' '}чтобы посмотреть свежие крео.
      </div>
    </div>
  )
}