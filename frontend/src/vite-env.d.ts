/// <reference types="vite/client" />

/**
 * Build-time flags. Declared rather than read loosely, so a typo in a flag name is a
 * compile error instead of a silently disabled feature.
 */
interface ImportMetaEnv {
  /** "1" in the GitHub Pages build, where there is no backend to talk to. */
  readonly VITE_STATIC_DEMO?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
