import type { VoidCubeDesktopApi } from '../../shared/contracts'

declare global {
  interface Window {
    voidcubeDesktop: VoidCubeDesktopApi
  }
}

export {}
