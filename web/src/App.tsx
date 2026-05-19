import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { LoginPage } from './pages/Login'
import { ConfigsPage } from './pages/Configs'
import { ModerationPage, TrashPage } from './pages/Moderation'
import { UsersPage } from './pages/Users'
import { PrivateRoute } from './components/PrivateRoute'
import { Layout } from './components/Layout'

const qc = new QueryClient({
  defaultOptions: {
    queries: { refetchOnWindowFocus: false, retry: 1 },
  },
})

function App() {
  return (
    <QueryClientProvider client={qc}>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route
            path="/*"
            element={
              <PrivateRoute>
                <Layout>
                  <Routes>
                    <Route path="/" element={<Navigate to="/moderation" />} />
                    <Route path="/moderation" element={<ModerationPage />} />
                    <Route path="/trash" element={<TrashPage />} />
                    <Route path="/configs" element={<ConfigsPage />} />
                    <Route path="/users" element={<UsersPage />} />
                  </Routes>
                </Layout>
              </PrivateRoute>
            }
          />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  )
}

export default App