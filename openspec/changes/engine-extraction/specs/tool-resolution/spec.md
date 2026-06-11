# tool-resolution

## ADDED Requirements

### Requirement: Freeze-aware tool resolution
`resolve_tool` SHALL resolve external tool names in this precedence order: (1) the user-data tools directory when configured, (2) under `sys.frozen`, the frozen bundle's tools directory; otherwise the directory containing `sys.executable` (current behavior), (3) bare name for PATH lookup. Under `sys.frozen`, `Path(sys.executable).parent` SHALL NOT be searched for console scripts (no console scripts exist there in a frozen app).

#### Scenario: User-data copy wins
- **WHEN** a tool exists in both the user-data tools directory and the bundle/interpreter directory
- **THEN** the user-data path is returned

#### Scenario: Frozen bundle resolution
- **WHEN** running frozen (`sys.frozen` set) and the tool exists in the bundle tools directory
- **THEN** the bundle path is returned and the interpreter directory is not consulted

#### Scenario: Unfrozen behavior preserved
- **WHEN** running unfrozen with the tool present next to `sys.executable`
- **THEN** that sibling path is returned (existing behavior)

#### Scenario: PATH fallback preserved
- **WHEN** the tool exists in none of the preferred directories
- **THEN** the bare tool name is returned for PATH lookup
