import { useState, type FormEvent } from 'react'
import { generate, type GenerateResponse } from './api'

const STOP_REASONS: Record<string, string> = {
  max_pages: 'reached the page limit',
  queue_empty: 'crawled every page it found',
  time: 'hit the time limit',
  errors: 'stopped after repeated errors',
}

export default function App() {
  const [url, setUrl] = useState('')
  const [monitor, setMonitor] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<GenerateResponse | null>(null)
  const [copied, setCopied] = useState(false)

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    if (!url.trim()) return
    setLoading(true)
    setError(null)
    setResult(null)
    try {
      setResult(await generate(url.trim(), monitor))
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }

  async function copy() {
    if (!result) return
    await navigator.clipboard.writeText(result.llms_txt)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }

  function download() {
    if (!result) return
    const blob = new Blob([result.llms_txt], { type: 'text/plain;charset=utf-8' })
    const link = document.createElement('a')
    link.href = URL.createObjectURL(blob)
    link.download = 'llms.txt'
    link.click()
    URL.revokeObjectURL(link.href)
  }

  return (
    <main className="page">
      <header>
        <h1>llms.txt Generator</h1>
        <p className="muted">
          Enter a website and get an <code>llms.txt</code> file that follows the{' '}
          <a href="https://llmstxt.org" target="_blank" rel="noreferrer">llmstxt.org</a> spec.
        </p>
      </header>

      <form onSubmit={onSubmit} className="card">
        <div className="row">
          <input
            type="text"
            inputMode="url"
            placeholder="https://example.com"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            disabled={loading}
            aria-label="Website URL"
            autoFocus
          />
          <button type="submit" disabled={loading || !url.trim()}>
            {loading ? 'Generating…' : 'Generate'}
          </button>
        </div>
        <label className="checkbox">
          <input type="checkbox" checked={monitor} onChange={(e) => setMonitor(e.target.checked)} disabled={loading} />
          Keep it up to date: check the site daily and regenerate when it changes
        </label>
      </form>

      {loading && (
        <p className="status">Crawling the site… this usually takes 10–45 seconds.</p>
      )}

      {error && <p className="error" role="alert">{error}</p>}

      {result && (
        <section className="card result">
          <div className="result-head">
            <p className="muted">
              {result.stats.pages} pages · {result.stats.duration_seconds}s · {STOP_REASONS[result.stats.stop_reason]}
            </p>
            <div className="actions">
              <button type="button" onClick={copy}>{copied ? 'Copied' : 'Copy'}</button>
              <button type="button" onClick={download}>Download</button>
            </div>
          </div>

          {result.stats.partial && (
            <p className="warning">The crawl was cut short, so this file may be missing some pages.</p>
          )}
          {result.problems.length > 0 && (
            <p className="warning">Spec check found problems: {result.problems.join(' ')}</p>
          )}
          {result.stable_url && (
            <p className="stable">
              Monitoring is on. This link always serves the latest version:{' '}
              <a href={result.stable_url} target="_blank" rel="noreferrer">{result.stable_url}</a>
            </p>
          )}

          <pre className="output">{result.llms_txt}</pre>
        </section>
      )}
    </main>
  )
}
