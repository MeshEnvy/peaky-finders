// @ts-check

const WA_DIALOG_WAIT_MS = 5000

/** @param {HTMLElement & { open?: boolean }|null|undefined} dialog */
export async function openWaDialog(dialog) {
  if (!dialog) return
  if (!customElements.get('wa-dialog')) {
    await Promise.race([
      customElements.whenDefined('wa-dialog'),
      new Promise((resolve) => setTimeout(resolve, WA_DIALOG_WAIT_MS)),
    ])
  }
  if (!customElements.get('wa-dialog')) {
    dialog.classList.add('wa-dialog-force-show')
  }
  dialog.open = true
}
