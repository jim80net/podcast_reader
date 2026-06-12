/**
 * Minimal typed DOM construction — the same discipline as the app renderer
 * (app/src/renderer/src/dom.ts): content always goes through `textContent`
 * and `append`, never `innerHTML`, because the popup is the token-holding
 * context and renders engine-supplied and page-derived strings (per U7).
 * The eslint fence (eslint.config.mjs) makes the rule mechanical.
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
