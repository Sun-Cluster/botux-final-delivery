'use client'
import { useEffect, useState } from 'react'

export default function TestPage() {
  const [data, setData] = useState<string>('waiting...')

  useEffect(() => {
    fetch('/api/v2/summary')
      .then(r => r.json())
      .then(d => setData(JSON.stringify({ equity: d.equity, cash: d.cash, positions: d.position_count }, null, 2)))
      .catch(e => setData('ERROR: ' + e.message))
  }, [])

  return (
    <div style={{ padding: 40, color: 'white', fontFamily: 'monospace', fontSize: 16 }}>
      <h1 style={{ fontSize: 24, marginBottom: 20 }}>BOTUX API Test</h1>
      <pre>{data}</pre>
    </div>
  )
}
