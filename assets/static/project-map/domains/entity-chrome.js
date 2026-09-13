// @ts-check

/**
 * Entity sidebar chrome: open/close, Sites/Land tabs, tag-filter apply.
 * @param {object} ctx
 */
export function createEntityChromeDomain(ctx) {
  const {
    store,
    getMap,
    getMapReady,
    getSites,
    mapShell,
    sitePanel,
    peakPanel,
    linkPanel,
    entityPanel,
    entityPanelToggle,
    entityPanelSitesPane,
    entityPanelLandPane,
    entityPanelTabs,
    mapToolSites,
    activeTagFilters,
    scheduleSaveMapState,
    applySiteLayerFilters,
    applyViewshedVisibilityForSite,
    refreshFilteredLinks,
    updatePinOverlays,
    renderEntityPanel,
    ensureViewshedsForNewlyVisibleSites,
    ensureAccessForVisibleSites,
    pruneActiveTagFilters,
    onEscape,
  } = ctx

  function syncEntityPanelToggles() {
    if (!mapToolSites) return
    const active = !!store.ui.entityPanelOpen
    mapToolSites.classList.toggle('active', active)
    mapToolSites.setAttribute('aria-pressed', active ? 'true' : 'false')
  }

  function syncMapViewport() {
    if (!mapShell) return
    const siteOpen = !!(sitePanel && !sitePanel.hidden)
    const peakOpen = !!(peakPanel && !peakPanel.hidden)
    const linkOpen = !!(linkPanel && !linkPanel.hidden)
    mapShell.classList.toggle('site-panel-open', siteOpen || peakOpen || linkOpen)
  }

  function setEntityTab(tab) {
    const next = tab === 'land' ? 'land' : 'sites'
    store.ui.entityPanelTab = next
    if (entityPanelSitesPane) entityPanelSitesPane.hidden = next !== 'sites'
    if (entityPanelLandPane) entityPanelLandPane.hidden = next !== 'land'
    for (const btn of entityPanelTabs) {
      const active = (btn.getAttribute('data-entity-tab') || 'sites') === next
      btn.classList.toggle('active', active)
      btn.setAttribute('aria-selected', active ? 'true' : 'false')
    }
    scheduleSaveMapState?.()
  }

  function setEntityPanelOpen(open) {
    store.ui.entityPanelOpen = !!open
    if (entityPanel) entityPanel.hidden = !store.ui.entityPanelOpen
    if (mapShell) mapShell.classList.toggle('entity-panel-open', store.ui.entityPanelOpen)
    if (entityPanelToggle) {
      entityPanelToggle.setAttribute(
        'aria-expanded',
        store.ui.entityPanelOpen ? 'true' : 'false',
      )
      entityPanelToggle.setAttribute(
        'aria-label',
        store.ui.entityPanelOpen ? 'Hide sites' : 'Show sites',
      )
    }
    syncEntityPanelToggles()
    scheduleSaveMapState?.()
    if (getMapReady()) {
      getMap()?.resize()
      requestAnimationFrame(() => updatePinOverlays?.())
    }
  }

  function toggleEntityPanel() {
    setEntityPanelOpen(!store.ui.entityPanelOpen)
  }

  function applyEntityVisibility() {
    applySiteLayerFilters?.()
    for (const site of getSites()) {
      applyViewshedVisibilityForSite?.(site.slug)
    }
    refreshFilteredLinks?.()
    renderEntityPanel?.()
  }

  function toggleTagFilter(tag) {
    const value = String(tag || '').trim()
    if (!value) return
    if (activeTagFilters.has(value)) activeTagFilters.delete(value)
    else activeTagFilters.add(value)
    pruneActiveTagFilters?.()
    applyEntityVisibility()
    ensureViewshedsForNewlyVisibleSites?.()
    ensureAccessForVisibleSites?.()
    scheduleSaveMapState?.()
  }

  function onTagFilterChange() {
    pruneActiveTagFilters?.()
    applyEntityVisibility()
    ensureViewshedsForNewlyVisibleSites?.()
    ensureAccessForVisibleSites?.()
    scheduleSaveMapState?.()
  }

  function bumpViewportEpoch() {
    if (!store?.ui) return
    store.ui.viewportEpoch = (store.ui.viewportEpoch || 0) + 1
  }

  function onViewportFilterChange() {
    bumpViewportEpoch()
    renderEntityPanel?.()
    scheduleSaveMapState?.()
  }

  function onMapMoveEndForEntityPanel() {
    if (!store?.ui?.filterByViewport && !store?.linkSolver?.panelOpen) return
    bumpViewportEpoch()
    if (store?.ui?.filterByViewport) renderEntityPanel?.()
  }

  function install() {
    entityPanelToggle?.addEventListener('click', () => {
      setEntityPanelOpen(!store.ui.entityPanelOpen)
    })
    mapToolSites?.addEventListener('click', () => toggleEntityPanel())
    for (const tabBtn of entityPanelTabs) {
      tabBtn.addEventListener('click', () => {
        setEntityTab(tabBtn.getAttribute('data-entity-tab') || 'sites')
      })
    }
    setEntityPanelOpen(store.ui.entityPanelOpen)
    setEntityTab(store.ui.entityPanelTab)
    document.addEventListener('keydown', (ev) => {
      if (ev.key === 'Escape') onEscape?.()
    })
  }

  return {
    syncEntityPanelToggles,
    syncMapViewport,
    setEntityTab,
    setEntityPanelOpen,
    toggleEntityPanel,
    applyEntityVisibility,
    toggleTagFilter,
    onTagFilterChange,
    onViewportFilterChange,
    onMapMoveEndForEntityPanel,
    install,
  }
}
