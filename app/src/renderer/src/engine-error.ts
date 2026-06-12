/**
 * Engine error presentation: IPC rejections arrive as
 * `Error invoking remote method '<channel>': Error: <original message>`, and
 * engine HTTP failures inside that as `engine request failed: <status>
 * <detail>` (EngineRequestError). These helpers recover the engine's
 * self-authored detail string and map settings-validation details onto form
 * fields for inline display (app-views spec: invalid setting rejected inline).
 */

export function extractEngineDetail(err: unknown): string {
  let message = err instanceof Error ? err.message : String(err)
  message = message.replace(/^Error invoking remote method '[^']*': (?:Error: )?/, '')
  message = message.replace(/^engine request failed: \d+ /, '')
  return message
}

export type SettingsErrorField = 'chapter_provider' | 'custom_provider_url'

/**
 * Best-effort field attribution for `PUT /v1/settings` 400 details
 * (`engine/app.py:put_settings` — provider membership and custom-URL
 * validation are the only engine-side rejections). Null means "show as a
 * general form error".
 */
export function settingsErrorField(detail: string): SettingsErrorField | null {
  if (/custom provider/i.test(detail)) return 'custom_provider_url'
  if (/chapter provider/i.test(detail)) return 'chapter_provider'
  return null
}
