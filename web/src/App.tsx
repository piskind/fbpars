import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { LoginPage } from './pages/Login'
import ConfigsPage from './pages/Configs'
import { ModerationPage, TrashPage } from './pages/Moderation'
import { UsersPage } from './pages/Users'
import { DashboardPage } from './pages/Dashboard'
import { PrivateRoute } from './components/PrivateRoute'
import { Layout } from './components/Layout'

import { ClientSignupPage } from './pages/client/Signup'
import { ClientLoginPage } from './pages/client/Login'
import { ClientVerifyPage } from './pages/client/Verify'
import { ClientHomePage } from './pages/client/Home'
import { ClientFeedPage } from './pages/client/Feed'
import { ClientLayout } from './components/client/ClientLayout'
import { ClientPrivateRoute } from './components/client/ClientPrivateRoute'

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
          <Route path="/signup" element={<ClientSignupPage />} />
          <Route path="/login" element={<ClientLoginPage />} />
          <Route path="/verify/:token" element={<ClientVerifyPage />} />

          <Route path="/admin/login" element={<LoginPage />} />
          <Route
            path="/admin/*"
            element={
              <PrivateRoute>
                <Layout>
                  <Routes>
                    <Route path="/" element={<DashboardPage />} />
                    <Route path="/moderation" element={<ModerationPage />} />
                    <Route path="/trash" element={<TrashPage />} />
                    <Route path="/configs" element={<ConfigsPage />} />
                    <Route path="/users" element={<UsersPage />} />
                    <Route path="*" element={<Navigate to="/admin" replace />} />
                  </Routes>
                </Layout>
              </PrivateRoute>
            }
          />

          <Route
            path="/*"
            element={
              <ClientPrivateRoute>
                <ClientLayout>
                  <Routes>
                    <Route path="/" element={<ClientHomePage />} />
                    <Route path="/feed" element={<ClientFeedPage />} />
                    <Route path="*" element={<Navigate to="/" replace />} />
                  </Routes>
                </ClientLayout>
              </ClientPrivateRoute>
            }
          />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  )
}

export default App