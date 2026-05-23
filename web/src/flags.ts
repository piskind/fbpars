export function countryFlag(code: string | null | undefined): string {
  if (!code || code.length !== 2) return ''
  const A = 0x1f1e6
  const upper = code.toUpperCase()
  const chars = [...upper].map((c) => A + c.charCodeAt(0) - 65)
  return String.fromCodePoint(...chars)
}