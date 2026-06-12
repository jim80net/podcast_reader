/**
 * Guard against out-of-order async completions: each `next()` call issues a
 * ticket and invalidates all earlier ones, so a view that refreshes on
 * several triggers only ever applies the response of its latest request.
 */
export class LatestGate {
  private generation = 0

  /** Start a request; the returned ticket reports whether it is still the latest. */
  next(): () => boolean {
    const generation = ++this.generation
    return () => generation === this.generation
  }
}
