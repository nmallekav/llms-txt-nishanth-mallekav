export interface CrawlStats {
  pages: number
  sitemaps: number
  duration_seconds: number
  stop_reason: 'max_pages' | 'queue_empty' | 'time' | 'errors'
  partial: boolean
}

export interface GenerateResponse {
  llms_txt: string
  url: string
  stats: CrawlStats
  problems: string[]
  site_id: string | null
  stable_url: string | null
}

export async function generate(url: string, monitor: boolean): Promise<GenerateResponse> {
  const res = await fetch('/api/generate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url, monitor }),
  })
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    const detail = body?.detail
    throw new Error(typeof detail === 'string' ? detail : `Request failed (HTTP ${res.status})`)
  }
  return res.json()
}
