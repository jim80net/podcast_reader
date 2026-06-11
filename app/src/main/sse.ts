/**
 * Incremental Server-Sent-Events parser for the engine's `/v1/events` stream.
 *
 * The engine emits `data: <json>\n\n` records plus `: keepalive` comment
 * heartbeats (`engine/app.py:events`). This parser handles exactly that
 * subset of the SSE grammar: `data:` fields (multi-line joined with `\n`)
 * and comment lines; `event:`/`id:`/`retry:` fields are ignored.
 */
export class SseParser {
  private buffer = ''
  private dataLines: string[] = []

  /** Feed a decoded chunk; returns the data payloads completed by it. */
  push(chunk: string): string[] {
    this.buffer += chunk
    const payloads: string[] = []
    for (;;) {
      const newline = this.buffer.indexOf('\n')
      if (newline === -1) break
      let line = this.buffer.slice(0, newline)
      this.buffer = this.buffer.slice(newline + 1)
      if (line.endsWith('\r')) line = line.slice(0, -1)
      if (line === '') {
        // Blank line: dispatch the accumulated event, if any.
        if (this.dataLines.length > 0) {
          payloads.push(this.dataLines.join('\n'))
          this.dataLines = []
        }
        continue
      }
      if (line.startsWith(':')) continue // comment / heartbeat
      if (line.startsWith('data:')) {
        let value = line.slice('data:'.length)
        if (value.startsWith(' ')) value = value.slice(1)
        this.dataLines.push(value)
      }
      // Other fields (event:, id:, retry:) are not used by the engine.
    }
    return payloads
  }
}
