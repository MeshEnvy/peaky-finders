// @ts-check

const DEFAULT_POS = { left: 12, top: 72 }

/**
 * Drag + persist position for a floating map panel.
 * @param {{
 *   panelEl: HTMLElement,
 *   headerEl: HTMLElement,
 *   shellEl: HTMLElement,
 *   posKey: string,
 *   getPos: () => { left: number, top: number }|null,
 *   setPos: (pos: { left: number, top: number }|null) => void,
 *   collapsed?: boolean,
 *   onCollapsedToggle?: (collapsed: boolean) => void,
 * }} opts
 */
export function mountFloatingPanel(opts) {
  const { panelEl, headerEl, shellEl, posKey, getPos, setPos, onCollapsedToggle } = opts

  function readStoredPos() {
    try {
      const raw = localStorage.getItem(posKey)
      if (!raw) return null
      const parsed = JSON.parse(raw)
      if (!Number.isFinite(parsed?.left) || !Number.isFinite(parsed?.top)) return null
      return { left: parsed.left, top: parsed.top }
    } catch (_) {
      return null
    }
  }

  function writeStoredPos(pos) {
    if (!pos) {
      localStorage.removeItem(posKey)
      return
    }
    localStorage.setItem(posKey, JSON.stringify(pos))
  }

  function clampPos(left, top) {
    const shellRect = shellEl.getBoundingClientRect()
    const panelRect = panelEl.getBoundingClientRect()
    const maxLeft = Math.max(0, shellRect.width - panelRect.width)
    const maxTop = Math.max(0, shellRect.height - panelRect.height)
    return {
      left: Math.min(Math.max(0, left), maxLeft),
      top: Math.min(Math.max(0, top), maxTop),
    }
  }

  function applyPos(pos) {
    const next = pos || DEFAULT_POS
    panelEl.style.left = `${next.left}px`
    panelEl.style.top = `${next.top}px`
    panelEl.style.right = 'auto'
  }

  function syncFromStore() {
    const pos = getPos() ?? readStoredPos()
    applyPos(pos)
    if (pos) setPos(pos)
  }

  syncFromStore()

  let dragStart = null

  headerEl.addEventListener('pointerdown', (ev) => {
    if (ev.button !== 0) return
    const target = /** @type {HTMLElement} */ (ev.target)
    if (target.closest('button, wa-button, select, input, a')) return
    ev.preventDefault()
    const rect = panelEl.getBoundingClientRect()
    const shellRect = shellEl.getBoundingClientRect()
    dragStart = {
      pointerId: ev.pointerId,
      offsetX: ev.clientX - rect.left,
      offsetY: ev.clientY - rect.top,
      shellLeft: shellRect.left,
      shellTop: shellRect.top,
    }
    headerEl.setPointerCapture(ev.pointerId)
    panelEl.classList.add('floating-panel--dragging')
  })

  headerEl.addEventListener('pointermove', (ev) => {
    if (!dragStart || dragStart.pointerId !== ev.pointerId) return
    const left = ev.clientX - dragStart.shellLeft - dragStart.offsetX
    const top = ev.clientY - dragStart.shellTop - dragStart.offsetY
    const clamped = clampPos(left, top)
    panelEl.style.left = `${clamped.left}px`
    panelEl.style.top = `${clamped.top}px`
  })

  function finishDrag(ev) {
    if (!dragStart || dragStart.pointerId !== ev.pointerId) return
    headerEl.releasePointerCapture(ev.pointerId)
    panelEl.classList.remove('floating-panel--dragging')
    const left = parseFloat(panelEl.style.left) || DEFAULT_POS.left
    const top = parseFloat(panelEl.style.top) || DEFAULT_POS.top
    const clamped = clampPos(left, top)
    setPos(clamped)
    writeStoredPos(clamped)
    dragStart = null
  }

  headerEl.addEventListener('pointerup', finishDrag)
  headerEl.addEventListener('pointercancel', finishDrag)

  headerEl.addEventListener('dblclick', (ev) => {
    if (/** @type {HTMLElement} */ (ev.target).closest('button, wa-button')) return
    setPos(null)
    writeStoredPos(null)
    applyPos(null)
  })

  return {
    syncPosition: syncFromStore,
    resetPosition() {
      setPos(null)
      writeStoredPos(null)
      applyPos(null)
    },
    toggleCollapsed(collapsed) {
      panelEl.classList.toggle('floating-panel--collapsed', collapsed)
      onCollapsedToggle?.(collapsed)
    },
  }
}
