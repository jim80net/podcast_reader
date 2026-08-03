export type ProductState =
  | { state: 'local' }
  | { state: 'online-unavailable' }
  | { state: 'online-free'; subject: string; refreshAfter: number; adPolicy: 'none' | 'house' }
  | { state: 'online-premium'; subject: string; refreshAfter: number }

type ObjectValue = Record<string, unknown>
const object = (value: unknown): ObjectValue => {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) throw new Error('invalid premium contract')
  return value as ObjectValue
}

export function reduceEntitlement(value: unknown, expectedSubject: string, now = Date.now()): ProductState {
  const root = object(value)
  const allowed = ['schema_version', 'subject', 'tier', 'entitlement', 'capabilities', 'flags_revision', 'evaluated_at', 'refresh_after']
  if (Object.keys(root).some((key) => !allowed.includes(key)) || root.schema_version !== 1 || root.subject !== expectedSubject) throw new Error('invalid premium contract')
  const capabilities = object(root.capabilities)
  const entitlement = object(root.entitlement)
  const capKeys = ['ad_policy', 'podcast_subscriptions', 'transcript_email', 'mobile_ad_free', 'topic_corpus']
  if (Object.keys(capabilities).length !== capKeys.length || Object.keys(capabilities).some((key) => !capKeys.includes(key))) throw new Error('invalid premium contract')
  if (Object.keys(entitlement).length !== 2 || !('source' in entitlement) || !Number.isSafeInteger(entitlement.revision) || Number(entitlement.revision) < 0 || !Number.isSafeInteger(root.flags_revision) || Number(root.flags_revision) < 0) throw new Error('invalid premium contract')
  if (capKeys.slice(1).some((key) => typeof capabilities[key] !== 'boolean')) throw new Error('invalid premium contract')
  const refreshAfter = Date.parse(String(root.refresh_after))
  const evaluatedAt = Date.parse(String(root.evaluated_at))
  if (!Number.isFinite(refreshAfter) || !Number.isFinite(evaluatedAt) || refreshAfter <= evaluatedAt || refreshAfter <= now || new Date(refreshAfter).toISOString().replace('.000Z', 'Z') !== root.refresh_after || new Date(evaluatedAt).toISOString().replace('.000Z', 'Z') !== root.evaluated_at) throw new Error('stale premium contract')
  if (root.tier === 'free' && entitlement.source === 'none' && (capabilities.ad_policy === 'none' || capabilities.ad_policy === 'house') && capabilities.podcast_subscriptions === false && capabilities.mobile_ad_free === false) return { state: 'online-free', subject: expectedSubject, refreshAfter, adPolicy: capabilities.ad_policy }
  if (root.tier === 'premium' && entitlement.source === 'test_purchase' && capabilities.ad_policy === 'none' && capabilities.mobile_ad_free === true) return { state: 'online-premium', subject: expectedSubject, refreshAfter }
  throw new Error('invalid premium contract')
}
