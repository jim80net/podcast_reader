import tseslint from 'typescript-eslint'

export default tseslint.config(
  { ignores: ['dist/', 'node_modules/'] },
  ...tseslint.configs.recommended,
  {
    rules: {
      '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_' }]
    }
  },
  {
    // The popup is the token-holding context: engine-supplied and
    // page-derived strings reach the DOM via textContent only (per U7).
    // Same mechanical fence as the app renderer (app/eslint.config.mjs).
    files: ['src/**/*.ts'],
    rules: {
      'no-restricted-properties': [
        'error',
        { property: 'innerHTML', message: 'Build DOM via dom.ts, never from strings (per U7).' },
        { property: 'outerHTML', message: 'Build DOM via dom.ts, never from strings (per U7).' },
        { property: 'insertAdjacentHTML', message: 'Build DOM via dom.ts, never from strings (per U7).' },
        { object: 'document', property: 'write', message: 'Build DOM via dom.ts, never from strings (per U7).' },
        { object: 'document', property: 'writeln', message: 'Build DOM via dom.ts, never from strings (per U7).' }
      ]
    }
  }
)
