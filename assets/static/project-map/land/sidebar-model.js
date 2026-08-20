/** Land sidebar model — pure normalize. */
export function normalizeLandSidebarInput(raw) {
  const folders = Array.isArray(raw?.folders)
    ? raw.folders
        .map((folder) => ({
          id: String(folder?.id || '').trim(),
          label: String(folder?.label || folder?.id || '').trim(),
          sources: Array.isArray(folder?.sources)
            ? folder.sources.map((sid) => String(sid).trim()).filter(Boolean)
            : [],
        }))
        .filter((folder) => folder.id)
    : []
  const unfiledSources = Array.isArray(raw?.unfiledSources)
    ? raw.unfiledSources.map((sid) => String(sid).trim()).filter(Boolean)
    : Array.isArray(raw?.unfiled_sources)
      ? raw.unfiled_sources.map((sid) => String(sid).trim()).filter(Boolean)
      : []
  return { folders, unfiledSources }
}
