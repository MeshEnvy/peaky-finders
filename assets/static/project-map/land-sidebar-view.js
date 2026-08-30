// @ts-check

/** Pure land-sidebar display helpers. No map or DOM state. */

export function landPathBasename(path) {
  let raw = path
  if (Array.isArray(raw)) raw = raw.join('/')
  raw = String(raw ?? '')
    .trim()
    .replace(/^data\//, '')
  const base = raw.split('/').pop() || raw
  return base.replace(/\.gdb$/i, '')
}

export function humanizeLandText(text) {
  return (
    String(text)
      .replace(/[_-]+-?\d{10,}$/, '')
      .replace(/[_-]+$/g, '')
      .replace(/_/g, ' ')
      .replace(/\s+/g, ' ')
      .trim() || String(text)
  )
}

export function friendlyLandLayerName(name) {
  return humanizeLandText(String(name).replace(/^BLM[_\s-]+/i, ''))
}

export function friendlyLandSourceTitle(source) {
  const explicit = String(source.label || '').trim()
  if (explicit && explicit !== source.id) return explicit
  return humanizeLandText(landPathBasename(source.path))
}

export function landSourceDisplayTitles(sources) {
  const titles = new Map()
  const groups = new Map()
  for (const source of sources) {
    const title = friendlyLandSourceTitle(source)
    titles.set(source.id, title)
    if (!groups.has(title)) groups.set(title, [])
    groups.get(title).push(source.id)
  }
  for (const ids of groups.values()) {
    if (ids.length <= 1) continue
    ids.forEach((id, idx) => {
      const suffix = id.match(/-(\d+)$/)
      const base = titles.get(id)
      titles.set(id, suffix ? `${base} (${suffix[1]})` : `${base} (${idx + 1})`)
    })
  }
  return titles
}

export function landLayerHasLabelField(spec) {
  return Boolean(spec?.labelField)
}

export function landLayerShortLabel(spec) {
  const id = String(spec.id || '').trim()
  if (id) {
    if (/^[a-z0-9]{1,8}$/i.test(id)) return id.toUpperCase()
    return humanizeLandText(id)
  }
  for (const filt of spec.include || []) {
    const values = (filt.values || []).filter(Boolean)
    if (values.length === 1) {
      if (filt.field === 'ABBR') return values[0]
      return filt.field ? `${filt.field}: ${values[0]}` : values[0]
    }
    if (values.length > 1) return values.join(', ')
  }
  return friendlyLandLayerName(spec.name)
}

export function landLayerDisplayName(spec) {
  const parts = [spec.name]
  const includeValues = (spec.include || [])
    .flatMap((filt) => (Array.isArray(filt.values) ? filt.values : []))
    .filter(Boolean)
  if (includeValues.length) parts.push(`(${includeValues.join(', ')})`)
  const excludeValues = (spec.exclude || [])
    .flatMap((filt) => (Array.isArray(filt.values) ? filt.values : []))
    .filter(Boolean)
  if (excludeValues.length) parts.push(`(−${excludeValues.join(', ')})`)
  return parts.join(' ')
}

export function landRuleSidebarText(spec) {
  if (spec.role === 'include') {
    const excluded = (spec.exclude || [])
      .flatMap((filt) => (Array.isArray(filt.values) ? filt.values : []))
      .filter(Boolean)
    if (excluded.length) {
      const preview = excluded.slice(0, 3).join(', ')
      return excluded.length > 3 ? `Remove: ${preview}…` : `Remove: ${preview}`
    }
    return 'Eligible land'
  }
  if (spec.role === 'exclude') return 'Blocked land'
  if (spec.role === 'aoi') return 'Project boundary'
  for (const filt of spec.include || []) {
    const values = (filt.values || []).filter(Boolean)
    if (values.length === 1) {
      return filt.field ? `${filt.field}: ${values[0]}` : String(values[0])
    }
    if (values.length > 1) return values.join(', ')
  }
  if (spec.id) return String(spec.id).toUpperCase()
  return 'Map overlay'
}

/** Bottom → top map paint rank. Exclude must sit above include. */
export function landMapStackRank(role) {
  const normalized = String(role || '')
    .trim()
    .toLowerCase()
  if (normalized === 'aoi') return 0
  if (normalized === 'include') return 1
  if (normalized === 'exclude') return 3
  if (normalized === 'eligible') return 4
  return 2
}

export function landLayerRoleBadgeSpec(role) {
  const normalized = String(role || '')
    .trim()
    .toLowerCase()
  if (normalized === 'aoi') {
    return {
      label: 'AOI',
      className: 'entity-panel__land-role-badge entity-panel__land-role-badge--aoi',
      title: 'Area of interest — unioned clip boundary for other layers',
    }
  }
  if (normalized === 'include') {
    return {
      label: 'Include',
      className: 'entity-panel__land-role-badge entity-panel__land-role-badge--include',
      title: 'Eligible land for goal seek (include − exclude)',
    }
  }
  if (normalized === 'exclude') {
    return {
      label: 'Exclude',
      className: 'entity-panel__land-role-badge entity-panel__land-role-badge--exclude',
      title: 'Subtracted from include layers for goal seek',
    }
  }
  if (normalized === 'eligible') {
    return {
      label: 'Eligible',
      className: 'entity-panel__land-role-badge entity-panel__land-role-badge--include',
      title: 'Include − exclude, clipped to the project AOI',
    }
  }
  return null
}
