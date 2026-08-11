import { describe, expect, it, vi } from 'vitest'
import type { Cookie } from 'electron'

vi.mock('electron', () => ({
  BrowserWindow: vi.fn(),
  session: {},
  shell: {}
}))

import { cookieHeaderForPlatform } from '../src/main/platform-login'

function cookie(name: string, value: string, domain: string): Cookie {
  return { name, value, domain } as Cookie
}

describe('platform login cookie collection', () => {
  it('keeps only cookies belonging to the selected platform root domain', () => {
    const header = cookieHeaderForPlatform([
      cookie('SESSDATA', 'session-token', '.bilibili.com'),
      cookie('bili_jct', 'csrf-token', 'api.bilibili.com'),
      cookie('stolen', 'no', 'evilbilibili.com'),
      cookie('unrelated', 'no', '.example.com')
    ], 'bilibili')

    expect(header).toBe('SESSDATA=session-token; bili_jct=csrf-token')
  })

  it('rejects unknown platform identifiers', () => {
    expect(cookieHeaderForPlatform([
      cookie('SESSDATA', 'session-token', '.bilibili.com')
    ], 'unknown')).toBe('')
  })
})
