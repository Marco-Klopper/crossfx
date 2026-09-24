import js from '@eslint/js'
import globals from 'globals'
import react from 'eslint-plugin-react'
import reactHooks from 'eslint-plugin-react-hooks'

/**
 * Flat ESLint config.
 *
 * The rule that earns its keep here is react-hooks/exhaustive-deps: the
 * bugs this repo's audit turned up in the screens were hook bugs —
 * effects reading state they had not declared, and `{ ...form }`
 * updaters capturing a stale render. Those are exactly what this
 * catches, and nothing had ever been run over these files.
 */
export default [
  { ignores: ['dist/**', 'node_modules/**', 'coverage/**'] },
  js.configs.recommended,
  {
    files: ['**/*.{js,jsx}'],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: 'module',
      globals: { ...globals.browser, ...globals.es2021 },
      parserOptions: { ecmaFeatures: { jsx: true } },
    },
    settings: { react: { version: 'detect' } },
    plugins: { react, 'react-hooks': reactHooks },
    rules: {
      ...react.configs.flat.recommended.rules,
      ...react.configs.flat['jsx-runtime'].rules,
      ...reactHooks.configs.recommended.rules,
      // The screens take props they render directly; propTypes on a
      // six-screen prototype would be ceremony, not safety.
      'react/prop-types': 'off',
      'no-unused-vars': ['error', { argsIgnorePattern: '^_' }],
    },
  },
  {
    // Vitest globals.
    files: ['**/*.test.{js,jsx}', 'src/test-setup.js'],
    languageOptions: { globals: { ...globals.node, ...globals.vitest } },
  },
]
