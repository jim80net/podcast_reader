import tseslint from 'typescript-eslint'

export default tseslint.config(
  { ignores: ['out/', 'dist/', 'node_modules/'] },
  ...tseslint.configs.recommended,
  {
    rules: {
      '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_' }]
    }
  },
  {
    // The renderer CSP allows 'unsafe-inline' script so the transcript
    // artifact's inline scroll-sync script can run inside its sandboxed
    // srcdoc iframe (CSP inherits into srcdoc). The compensating control is
    // that app chrome NEVER builds DOM from strings — dom.ts is the sole
    // construction path. This fence makes that mechanical (R4).
    files: ['src/**/*.ts'],
    rules: {
      'no-restricted-properties': [
        'error',
        { property: 'innerHTML', message: 'Build DOM via dom.ts, never from strings (CSP compensation).' },
        { property: 'outerHTML', message: 'Build DOM via dom.ts, never from strings (CSP compensation).' },
        { property: 'insertAdjacentHTML', message: 'Build DOM via dom.ts, never from strings (CSP compensation).' },
        { object: 'document', property: 'write', message: 'Build DOM via dom.ts, never from strings (CSP compensation).' },
        { object: 'document', property: 'writeln', message: 'Build DOM via dom.ts, never from strings (CSP compensation).' }
      ]
    }
  },
  {
    // electron-builder reads its config via require(); CommonJS is intentional.
    files: ['**/*.cjs'],
    languageOptions: { sourceType: 'commonjs' },
    rules: { '@typescript-eslint/no-require-imports': 'off' }
  }
)
