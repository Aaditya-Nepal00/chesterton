import { useEffect, useState } from 'react'

type Health = {
  status: string
  app_name: string
  gemini_backend: string
  model_screen: string | null
  model_main: string | null
  github_app_configured: boolean
}

function App() {
  const [health, setHealth] = useState<Health | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetch('/api/health')
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then(setHealth)
      .catch((e: Error) => setError(e.message))
  }, [])

  return (
    <main className="p-8 font-sans">
      <h1 className="text-2xl font-bold">{health?.app_name ?? 'Chesterton'}</h1>
      <h2 className="mt-4 font-semibold">/api/health</h2>
      {error && <p className="text-red-600">Error: {error}</p>}
      <pre className="mt-2 rounded bg-gray-100 p-4 text-sm">
        {health ? JSON.stringify(health, null, 2) : error ? '' : 'Loading…'}
      </pre>
    </main>
  )
}

export default App
