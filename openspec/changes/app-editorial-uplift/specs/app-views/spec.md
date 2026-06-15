# app-views Specification (delta)

## ADDED Requirements

### Requirement: Cohesive editorial visual design
The renderer views (Library, Reader, New, Settings, and the first-run Setup) SHALL present a cohesive editorial visual design — serif display titles with a `system-ui` body/UI typeface, a warm light palette and a calm dark palette, a consistent type/spacing scale, and a single brand-blue accent — rather than the default theme. The design SHALL be driven by shared design tokens so light and dark are both first-class and consistent across views. It SHALL meet WCAG AA contrast for text and controls on both palettes, keep a visible keyboard focus indicator on interactive elements, and confine any added motion behind `prefers-reduced-motion`. The visual change SHALL be applied by restyling the existing semantic elements: element roles, headings, and visible text SHALL be preserved (no DOM restructuring), and the sandboxed transcript artifact's own rendering SHALL NOT be altered (only the app chrome around the Reader is restyled). No font asset SHALL be bundled and no new network/CSP surface SHALL be introduced.

#### Scenario: Editorial design applied across views in both themes
- **WHEN** any of Library / Reader / New / Settings / Setup is shown in light or dark mode
- **THEN** it renders in the cohesive editorial design (serif titles, themed palette, brand-blue accent) driven by the shared tokens

#### Scenario: DOM semantics preserved for assistive tech and tests
- **WHEN** the views are inspected after the uplift
- **THEN** element roles, headings, and visible text are unchanged from before (restyle only), so existing assistive-tech navigation and the Playwright selectors still resolve

#### Scenario: Sandboxed artifact untouched
- **WHEN** the Reader displays a transcript
- **THEN** the app chrome around it reflects the editorial design while the artifact inside the sandboxed iframe renders exactly as its own `html.py` output dictates

#### Scenario: Accessibility maintained
- **WHEN** text, muted text, and controls are rendered on the light and dark palettes
- **THEN** they meet WCAG AA contrast, interactive elements show a visible focus state, and any transition is disabled under `prefers-reduced-motion`
