import { useState, type FormEvent } from 'react'
import { generate, type GenerateResponse } from './api'

function timeAgo(iso: string): string {
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60000)
  if (minutes < 1) return 'just now'
  if (minutes < 60) return `${minutes} min ago`
  const hours = Math.round(minutes / 60)
  return hours < 24 ? `${hours} h ago` : `${Math.round(hours / 24)} d ago`
}

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

  async function run(refresh: boolean) {
    setLoading(true)
    setError(null)
    setResult(null)
    try {
      setResult(await generate(url.trim(), monitor, refresh))
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault()
    if (url.trim()) run(false)
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
              {result.stats.pages} pages · {result.cached ? 'served instantly' : `${result.stats.duration_seconds}s`} · {STOP_REASONS[result.stats.stop_reason]}
            </p>
            <div className="actions">
              <button type="button" onClick={copy}>{copied ? 'Copied' : 'Copy'}</button>
              <button type="button" onClick={download}>Download</button>
            </div>
          </div>

          {result.cached && (
            <div className="cached">
              <span>
                Saved version: crawled {timeAgo(result.crawled_at)}
                {result.checked_at && <>, last checked for changes {timeAgo(result.checked_at)}</>}.
              </span>
              <button type="button" onClick={() => run(true)}>Regenerate now</button>
            </div>
          )}
          {result.stats.partial && (
            <p className="warning">The crawl was cut short, so this file may be missing some pages.</p>
          )}
          {result.problems.length > 0 && (
            <p className="warning">Spec check found problems: {result.problems.join(' ')}</p>
          )}
          <div className="quality">
            <p className="quality-score">
              Quality <strong>{result.quality.score}</strong>/100
            </p>
            <ul>
              {result.quality.checks.map((check) => (
                <li key={check.name} className={check.status}>
                  <span className="icon" aria-hidden="true">{check.status === 'ok' ? '✓' : '!'}</span>
                  <span><strong>{check.name}:</strong> {check.message}</span>
                </li>
              ))}
            </ul>
          </div>

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
