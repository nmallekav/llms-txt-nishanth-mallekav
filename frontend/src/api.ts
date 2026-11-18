export interface CrawlStats {
  pages: number
  sitemaps: number
  duration_seconds: number
  stop_reason: 'max_pages' | 'queue_empty' | 'time'
  partial: boolean
}

export interface QualityCheck {
  name: string
  status: 'ok' | 'warn'
  message: string
}

export interface Quality {
  score: number
  checks: QualityCheck[]
}

export interface GenerateResponse {
  llms_txt: string
  url: string
  stats: CrawlStats | null // null for a saved copy
  problems: string[]
  quality: Quality | null // null for a saved copy
  site_id: string | null
  stable_url: string | null
  cached_at: string | null // set when this is a saved copy: when it was crawled
}

// refresh = true crawls again even if a recent saved copy exists.
export async function generate(url: string, monitor: boolean, refresh = false): Promise<GenerateResponse> {
  const res = await fetch('/api/generate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url, monitor, refresh }),
  })
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    const detail = body?.detail
    throw new Error(typeof detail === 'string' ? detail : `Request failed (HTTP ${res.status})`)
  }
  return res.json()
}
