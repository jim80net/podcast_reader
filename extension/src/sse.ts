/**
 * Incremental Server-Sent-Events parser for the engine's `/v1/events`
 * stream — the same subset as the app's consumer (app/src/main/sse.ts):
 * `data:` fields (multi-line joined with `\n`) and comment heartbeats;
 * `event:`/`id:`/`retry:` are ignored because the engine never sends them.
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
        if (this.dataLines.length > 0) {
          payloads.push(this.dataLines.join('\n'))
          this.dataLines = []
        }
        continue
      }
      if (line.startsWith(':')) continue
      if (line.startsWith('data:')) {
        let value = line.slice('data:'.length)
        if (value.startsWith(' ')) value = value.slice(1)
        this.dataLines.push(value)
      }
    }
    return payloads
  }
}
