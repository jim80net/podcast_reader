/**
 * Minimal typed DOM construction (design decision 1: no framework). Content
 * always goes through `textContent`/`append` — never `innerHTML` — so the
 * page CSP's `'unsafe-inline'` script allowance (needed for the Reader's
 * srcdoc artifact, see index.html) can never be reached by engine-provided
 * strings rendered in the app chrome.
 */

export interface ElProps {
  class?: string
  text?: string
  attrs?: Record<string, string>
}

export function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  props: ElProps = {},
  ...children: (Node | string)[]
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag)
  if (props.class !== undefined) node.className = props.class
  if (props.text !== undefined) node.textContent = props.text
  if (props.attrs !== undefined) {
    for (const [name, value] of Object.entries(props.attrs)) node.setAttribute(name, value)
  }
  node.append(...children)
  return node
}
