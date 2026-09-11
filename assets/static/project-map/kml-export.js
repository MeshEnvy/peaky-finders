// @ts-check

/** @param {string} text */
function xmlEscape(text) {
  return String(text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
}

/**
 * @param {object} site
 * @returns {{ name: string, lat: number, lon: number, alt: number, description: string }}
 */
export function siteToKmlPlacemark(site) {
  const slug = String(site.slug || '')
  const name = `${site.name || slug} (${slug})`
  const lines = [`slug: ${slug}`]
  const tags = Array.isArray(site.tags) ? site.tags.filter(Boolean) : []
  if (tags.length) lines.push(`tags: ${tags.join(', ')}`)
  if (site.node) lines.push(`node: ${site.node}`)
  if (typeof site.description === 'string' && site.description.trim()) {
    lines.push(site.description.trim())
  }
  const alt = Number.isFinite(Number(site.height_m)) ? Number(site.height_m) : 0
  return {
    name,
    lat: Number(site.lat),
    lon: Number(site.lon),
    alt,
    description: lines.join('\n'),
  }
}

/**
 * @param {string} documentName
 * @param {object[]} sites
 */
export function buildSitesKml(documentName, sites) {
  const placemarks = sites.map((site) => siteToKmlPlacemark(site))
  const parts = [
    '<?xml version="1.0" encoding="UTF-8"?>',
    '<kml xmlns="http://www.opengis.net/kml/2.2">',
    '  <Document>',
    `    <name>${xmlEscape(documentName)}</name>`,
  ]
  for (const pm of placemarks) {
    parts.push('    <Placemark>')
    parts.push(`      <name>${xmlEscape(pm.name)}</name>`)
    if (pm.description) {
      parts.push(`      <description>${xmlEscape(pm.description)}</description>`)
    }
    parts.push('      <Point>')
    parts.push(`        <coordinates>${pm.lon},${pm.lat},${pm.alt}</coordinates>`)
    parts.push('      </Point>')
    parts.push('    </Placemark>')
  }
  parts.push('  </Document>', '</kml>', '')
  return parts.join('\n')
}

/**
 * @param {string} kml
 * @param {string} fileName
 */
export function downloadKmlFile(kml, fileName) {
  const blob = new Blob([kml], { type: 'application/vnd.google-earth.kml+xml' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = fileName
  anchor.style.display = 'none'
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(url)
}

/**
 * @param {object[]} sites
 * @param {string} fileName
 * @param {string} [documentName]
 */
export function downloadSitesKml(sites, fileName, documentName = 'viewport') {
  downloadKmlFile(buildSitesKml(documentName, sites), fileName)
}
