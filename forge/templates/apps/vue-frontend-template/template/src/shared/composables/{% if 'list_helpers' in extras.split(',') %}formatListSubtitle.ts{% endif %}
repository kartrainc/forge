export interface FormatListSubtitleOpts {
  /** Server-reported true total (independent of pagination/filter). */
  total: number
  /**
   * Optional rendered/filtered count. When provided AND smaller than total,
   * the subtitle switches to "X of Y · qualifier" so users see that filters
   * are narrowing the view.
   */
  filtered?: number
  noun: { singular: string; plural: string }
  /** e.g. "5 healthy", "6 enabled", "6 active". Optional. */
  qualifier?: string
}

/**
 * Build a uniform list-page subtitle.
 *
 * Examples:
 *   { total: 7, noun: {singular:'item',plural:'items'}, qualifier:'6 enabled' }
 *     → "7 items · 6 enabled"
 *   { total: 7, filtered: 3, noun: {singular:'item',plural:'items'}, qualifier:'2 enabled' }
 *     → "3 of 7 items · 2 enabled"
 *   { total: 0, ... } → undefined  (page has no items, hide the subtitle)
 */
export function formatListSubtitle(opts: FormatListSubtitleOpts): string | undefined {
  if (opts.total <= 0) return undefined

  const noun = opts.total === 1 ? opts.noun.singular : opts.noun.plural

  let lead: string
  if (opts.filtered !== undefined && opts.filtered < opts.total) {
    lead = `${opts.filtered} of ${opts.total} ${noun}`
  } else {
    lead = `${opts.total} ${noun}`
  }

  if (!opts.qualifier) return lead
  return `${lead} · ${opts.qualifier}`
}
