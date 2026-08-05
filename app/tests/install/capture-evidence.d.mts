import type { CDPSession, Locator, Page } from '@playwright/test'

export interface CaptureEvidence {
  width: number
  height: number
  pixelVariance: number
}

export interface PngCaptureExpectation {
  label?: string
  expectedWidth: number
  expectedHeight: number
  minimumVariance?: number
}

export function assertPngCapture(
  bytes: Buffer,
  expectation: PngCaptureExpectation
): CaptureEvidence

export function capturePageEvidence(
  page: Page,
  options: {
    path: string
    fullPage?: boolean
    caret?: 'hide' | 'initial'
    label?: string
  }
): Promise<CaptureEvidence>

export function captureScaledPageEvidence(
  page: Page,
  cdp: CDPSession,
  options: {
    path: string
    deviceScaleFactor: number
    fullPage?: boolean
    caret?: 'hide' | 'initial'
    label?: string
  }
): Promise<CaptureEvidence & { devicePixelRatio: number }>

export function captureLocatorEvidence(
  locator: Locator,
  options: { path: string; label?: string }
): Promise<CaptureEvidence>
