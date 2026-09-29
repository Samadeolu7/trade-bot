import {
  AreaSeries,
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  LineStyle,
  createChart,
  createSeriesMarkers,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from 'lightweight-charts'
import { useEffect, useRef, useState } from 'react'
import type { Candle } from '../api/hooks'
import { money } from '../lib/format'

function token(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim()
}

/** Re-reads the CSS tokens when the colour scheme changes. */
function useThemeVersion(): number {
  const [version, setVersion] = useState(0)
  useEffect(() => {
    const media = window.matchMedia('(prefers-color-scheme: light)')
    const bump = () => setVersion((v) => v + 1)
    media.addEventListener('change', bump)
    const observer = new MutationObserver(bump)
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
    return () => {
      media.removeEventListener('change', bump)
      observer.disconnect()
    }
  }, [])
  return version
}

function baseOptions() {
  return {
    layout: {
      background: { type: ColorType.Solid, color: token('--raised') },
      textColor: token('--muted'),
      fontFamily: 'IBM Plex Sans, system-ui, sans-serif',
      fontSize: 12,
      attributionLogo: false,
    },
    grid: {
      vertLines: { color: token('--grid') },
      horzLines: { color: token('--grid') },
    },
    rightPriceScale: { borderColor: token('--line') },
    timeScale: { borderColor: token('--line'), timeVisible: true, secondsVisible: false },
    crosshair: { mode: CrosshairMode.Normal },
    autoSize: true,
  }
}

export type ChartMarker = {
  time: number // epoch seconds
  side: 'buy' | 'sell'
  text: string
}

export type ChartLine = {
  price: number
  label: string
  kind: 'stop' | 'target' | 'entry' | 'order'
}

const TIMEFRAME_SECONDS: Record<string, number> = { '1h': 3600, '4h': 14400, '1d': 86400 }

/**
 * Candles with the book's own activity drawn on them: arrows for fills,
 * horizontal lines for the average entry, engine-held stop and target,
 * and resting orders. This is where "what is the bot doing" is visible.
 */
export function PriceChart({
  candles,
  timeframe,
  markers = [],
  lines = [],
  height = 420,
}: {
  candles: Candle[]
  timeframe: string
  markers?: ChartMarker[]
  lines?: ChartLine[]
  height?: number
}) {
  const el = useRef<HTMLDivElement>(null)
  const chart = useRef<IChartApi | null>(null)
  const series = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const priceLines = useRef<IPriceLine[]>([])
  const markerApi = useRef<ReturnType<typeof createSeriesMarkers<Time>> | null>(null)
  const theme = useThemeVersion()

  useEffect(() => {
    if (!el.current) return
    const c = createChart(el.current, baseOptions())
    const s = c.addSeries(CandlestickSeries, {
      upColor: token('--up'),
      downColor: token('--down'),
      borderVisible: false,
      wickUpColor: token('--up'),
      wickDownColor: token('--down'),
      priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
    })
    chart.current = c
    series.current = s
    markerApi.current = createSeriesMarkers(s, [])
    return () => {
      c.remove()
      chart.current = null
      series.current = null
      priceLines.current = []
    }
  }, [theme])

  useEffect(() => {
    series.current?.setData(
      candles.map((c) => ({ time: c.time as UTCTimestamp, open: c.open, high: c.high, low: c.low, close: c.close })),
    )
  }, [candles, theme])

  useEffect(() => {
    // markers must sit on a bar: snap each fill to the candle it happened in
    const step = TIMEFRAME_SECONDS[timeframe] ?? 3600
    const sorted: SeriesMarker<Time>[] = markers
      .map((m) => ({
        time: (Math.floor(m.time / step) * step) as UTCTimestamp,
        position: m.side === 'buy' ? ('belowBar' as const) : ('aboveBar' as const),
        shape: m.side === 'buy' ? ('arrowUp' as const) : ('arrowDown' as const),
        color: token(m.side === 'buy' ? '--up' : '--down'),
        text: m.text,
      }))
      .sort((a, b) => (a.time as number) - (b.time as number))
    markerApi.current?.setMarkers(sorted)
  }, [markers, timeframe, theme])

  useEffect(() => {
    const s = series.current
    if (!s) return
    for (const line of priceLines.current) s.removePriceLine(line)
    const colors = { stop: '--down', target: '--up', entry: '--ink-2', order: '--paper' }
    priceLines.current = lines.map((l) =>
      s.createPriceLine({
        price: l.price,
        title: l.label,
        color: token(colors[l.kind]),
        lineWidth: 1,
        lineStyle: l.kind === 'entry' ? LineStyle.Solid : LineStyle.Dashed,
        axisLabelVisible: true,
      }),
    )
  }, [lines, theme])

  return <div ref={el} style={{ height }} className="w-full" />
}

/**
 * One equity series with a crosshair readout. Several books are shown as
 * separate charts, never one chart with two scales.
 */
export function EquityChart({
  points,
  height = 220,
  baseline,
}: {
  points: { time: number; equity: number }[]
  height?: number
  baseline?: number
}) {
  const el = useRef<HTMLDivElement>(null)
  const theme = useThemeVersion()
  const [hover, setHover] = useState<{ time: number; value: number } | null>(null)

  useEffect(() => {
    if (!el.current) return
    const c = createChart(el.current, {
      ...baseOptions(),
      crosshair: { mode: CrosshairMode.Magnet, vertLine: { labelVisible: false } },
    })
    const s = c.addSeries(AreaSeries, {
      lineColor: token('--paper'),
      lineWidth: 2,
      topColor: `${token('--paper')}33`,
      bottomColor: `${token('--paper')}00`,
      priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
      lastValueVisible: true,
    })
    // snapshots can share a second; the chart needs strictly increasing time
    const seen = new Set<number>()
    const data = points
      .filter((p) => (seen.has(p.time) ? false : (seen.add(p.time), true)))
      .map((p) => ({ time: p.time as UTCTimestamp, value: p.equity }))
    s.setData(data)
    if (baseline != null) {
      s.createPriceLine({ price: baseline, color: token('--muted'), lineWidth: 1, lineStyle: LineStyle.Dotted, title: 'start' })
    }
    c.timeScale().fitContent()
    c.subscribeCrosshairMove((param) => {
      const v = param.seriesData.get(s) as { value: number } | undefined
      setHover(param.time && v ? { time: param.time as number, value: v.value } : null)
    })
    return () => c.remove()
  }, [points, baseline, theme])

  return (
    <div className="relative">
      <div className="pointer-events-none absolute left-3 top-2 z-10 text-[12px] text-ink-2">
        {hover ? (
          <span className="num">
            {new Date(hover.time * 1000).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
            {'  '}
            <span className="font-semibold text-ink">{money(hover.value)}</span>
          </span>
        ) : null}
      </div>
      <div ref={el} style={{ height }} className="w-full" />
    </div>
  )
}
