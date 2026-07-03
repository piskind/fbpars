import { useEffect, useRef, useState } from 'react'
import { ChevronDown, ChevronLeft, ChevronRight } from 'lucide-react'

// value: ISO даты 'yyyy-mm-dd' или '' — совместимо с started_from / started_to
type Props = {
  from: string
  to: string
  onChange: (from: string, to: string) => void
}

const MONTHS = [
  'Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
  'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь',
]
const WEEK = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']

function iso(d: Date): string {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}
function fmt(s: string): string {
  if (!s) return ''
  const [y, m, d] = s.split('-')
  return `${d}.${m}.${y}`
}
function addMonths(d: Date, n: number): Date {
  return new Date(d.getFullYear(), d.getMonth() + n, d.getDate())
}

type Preset = { label: string; range: () => [string, string] }

function presets(): Preset[] {
  const today = new Date()
  const t = iso(today)
  const daysAgo = (n: number) => {
    const d = new Date()
    d.setDate(d.getDate() - n)
    return iso(d)
  }
  const firstOfMonth = (off = 0) =>
    iso(new Date(today.getFullYear(), today.getMonth() + off, 1))
  const lastOfMonth = (off = 0) =>
    iso(new Date(today.getFullYear(), today.getMonth() + off + 1, 0))
  return [
    { label: 'Сегодня', range: () => [t, t] },
    { label: 'Вчера', range: () => [daysAgo(1), daysAgo(1)] },
    { label: 'За неделю', range: () => [daysAgo(6), t] },
    { label: '30 дней', range: () => [daysAgo(29), t] },
    { label: 'В этом месяце', range: () => [firstOfMonth(0), t] },
    { label: 'В прошлом месяце', range: () => [firstOfMonth(-1), lastOfMonth(-1)] },
    { label: 'За 3 месяца', range: () => [iso(addMonths(today, -3)), t] },
    { label: 'За 6 месяцев', range: () => [iso(addMonths(today, -6)), t] },
    { label: 'За 12 месяцев', range: () => [iso(addMonths(today, -12)), t] },
    { label: 'Всё время', range: () => ['', ''] },
  ]
}

function MonthGrid({
  base,
  from,
  to,
  onPick,
}: {
  base: Date
  from: string
  to: string
  onPick: (s: string) => void
}) {
  const year = base.getFullYear()
  const month = base.getMonth()
  const firstDay = new Date(year, month, 1)
  // Пн=0
  const lead = (firstDay.getDay() + 6) % 7
  const daysInMonth = new Date(year, month + 1, 0).getDate()
  const cells: (Date | null)[] = []
  for (let i = 0; i < lead; i++) cells.push(null)
  for (let d = 1; d <= daysInMonth; d++) cells.push(new Date(year, month, d))

  return (
    <div className="w-[220px]">
      <div className="text-center text-sm font-medium mb-2">
        {MONTHS[month]} {year}
      </div>
      <div className="grid grid-cols-7 gap-0.5">
        {WEEK.map((w) => (
          <div key={w} className="text-[10px] text-gray-400 text-center py-1">{w}</div>
        ))}
        {cells.map((d, i) => {
          if (!d) return <div key={i} />
          const s = iso(d)
          const inRange = from && to && s >= from && s <= to
          const isEdge = s === from || s === to
          return (
            <button
              key={i}
              type="button"
              onClick={() => onPick(s)}
              className={`text-xs h-7 rounded ${
                isEdge
                  ? 'bg-blue-600 text-white font-medium'
                  : inRange
                  ? 'bg-blue-100 text-blue-700'
                  : 'hover:bg-gray-100'
              }`}
            >
              {d.getDate()}
            </button>
          )
        })}
      </div>
    </div>
  )
}

export function DateRangePicker({ from, to, onChange }: Props) {
  const [open, setOpen] = useState(false)
  const [pickingStart, setPickingStart] = useState(true)
  const [viewMonth, setViewMonth] = useState(() => new Date())
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  const label = from || to ? `${fmt(from) || '…'} – ${fmt(to) || '…'}` : 'Дата создания'

  const pick = (s: string) => {
    if (pickingStart || !from || (to && from)) {
      onChange(s, '')
      setPickingStart(false)
    } else {
      if (s < from) onChange(s, from)
      else onChange(from, s)
      setPickingStart(true)
    }
  }

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1.5 px-3 py-2 border rounded-lg text-sm bg-white hover:border-gray-400 min-w-[190px] justify-between"
      >
        <span className={`truncate ${from || to ? '' : 'text-gray-500'}`}>{label}</span>
        <ChevronDown className="w-3.5 h-3.5 text-gray-400 shrink-0" />
      </button>
      {open && (
        <div className="absolute z-50 mt-1 bg-white border rounded-xl shadow-lg flex">
          <div className="border-r p-2 w-40 shrink-0">
            {presets().map((p) => (
              <button
                key={p.label}
                type="button"
                onClick={() => {
                  const [f, t] = p.range()
                  onChange(f, t)
                  setPickingStart(true)
                }}
                className="block w-full text-left px-2 py-1.5 text-xs rounded hover:bg-gray-100"
              >
                {p.label}
              </button>
            ))}
          </div>
          <div className="p-3">
            <div className="flex items-center justify-between mb-1">
              <button
                type="button"
                onClick={() => setViewMonth((m) => addMonths(m, -1))}
                className="p-1 rounded hover:bg-gray-100"
              >
                <ChevronLeft className="w-4 h-4 text-gray-500" />
              </button>
              <button
                type="button"
                onClick={() => setViewMonth((m) => addMonths(m, 1))}
                className="p-1 rounded hover:bg-gray-100"
              >
                <ChevronRight className="w-4 h-4 text-gray-500" />
              </button>
            </div>
            <div className="flex gap-4">
              <MonthGrid base={viewMonth} from={from} to={to} onPick={pick} />
              <MonthGrid base={addMonths(viewMonth, 1)} from={from} to={to} onPick={pick} />
            </div>
            <div className="flex justify-end gap-2 mt-2 pt-2 border-t">
              <button
                type="button"
                onClick={() => {
                  onChange('', '')
                  setPickingStart(true)
                }}
                className="text-xs text-gray-500 hover:text-gray-700 px-2 py-1"
              >
                Сбросить
              </button>
              <button
                type="button"
                onClick={() => setOpen(false)}
                className="text-xs bg-blue-600 text-white px-3 py-1 rounded-lg hover:bg-blue-700"
              >
                Готово
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
