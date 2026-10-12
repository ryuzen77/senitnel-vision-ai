import { afterEach, it, expect, vi } from 'vitest'
import * as api from './preparedSourceApi'
import { sourceResponse, privateCaps, preparedSnapshot, validConfiguration, sourceId, intentId } from '../testUtils/appFetchStub'
afterEach(() => vi.unstubAllGlobals())
it.each(Object.entries(api.SOURCE_MESSAGES))('maps public category %s without retaining raw input', async (category, message) => { const fetch = vi.fn().mockResolvedValue(sourceResponse({ category, detail: 'PRIVATE_MARKER' }, 409)); vi.stubGlobal('fetch', fetch); await expect(api.getCurrentSource()).rejects.toThrow(message); expect(fetch).toHaveBeenCalledTimes(1) })
it.each(['write_finished', 'invented_category'])('does not map internal/unknown %s', async (category) => { vi.stubGlobal('fetch', vi.fn().mockResolvedValue(sourceResponse({ category }, 409))); await expect(api.getCurrentSource()).rejects.toThrow('Respons aplikasi tidak dapat dibaca') })
it('validates capabilities and current shape, missing fields fail closed', async () => { vi.stubGlobal('fetch', vi.fn().mockResolvedValue(sourceResponse(privateCaps))); expect((await api.getCapabilities()).mode).toBe('private'); vi.stubGlobal('fetch', vi.fn().mockResolvedValue(sourceResponse(preparedSnapshot()))); expect((await api.getCurrentSource())?.source_id).toBe(sourceId); vi.stubGlobal('fetch', vi.fn().mockResolvedValue(sourceResponse({ state: 'PREPARED' }))); await expect(api.getCurrentSource()).rejects.toThrow('Respons aplikasi tidak dapat dibaca') })
it('configuration sends exactly seven keys and preserves precision', async () => { const fetch = vi.fn().mockResolvedValue(sourceResponse({ revision: 1, ready: true, canonical: validConfiguration })); vi.stubGlobal('fetch', fetch); await api.saveConfiguration({ source_id: sourceId, generation: 1 }, null, validConfiguration); expect(Object.keys(JSON.parse(fetch.mock.calls[0][1].body)).sort()).toEqual(['generation', 'expected_revision', 'frame_width', 'frame_height', 'line', 'inside', 'zones'].sort()) })
it('caps the UTF-8 envelope before any request', async () => { const fetch = vi.fn(); vi.stubGlobal('fetch', fetch); await expect(api.saveConfiguration({ source_id: sourceId, generation: 1 }, null, { ...validConfiguration, zones: [{ ...validConfiguration.zones[0], name: 'é'.repeat(2200) }] })).rejects.toThrow(api.SOURCE_MESSAGES.configuration_too_large); expect(fetch).not.toHaveBeenCalled() })
it('pins canonical UUID and explains missing secure-context capability', () => { vi.stubGlobal('crypto', { randomUUID: () => intentId }); expect(api.operationId()).toBe(intentId); vi.stubGlobal('crypto', {}); expect(api.privateMutationAvailable()).toBe(false); expect(() => api.operationId()).toThrow('HTTPS atau localhost') })
it('malformed success and connection errors differ', async () => { vi.stubGlobal('fetch', vi.fn().mockResolvedValue(sourceResponse(null))); await expect(api.getCapabilities()).rejects.toThrow('Respons aplikasi tidak dapat dibaca'); vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('PRIVATE_MARKER'))); await expect(api.getCapabilities()).rejects.toThrow('Layanan tidak dapat dihubungi') })
it.each(['configuration', 'revision', 'attempt', 'line.p2', 'zones[0].rect', 'inside'])('maps known error field %s without a raw value', async (field) => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(sourceResponse({ category: 'invalid_geometry', field, detail: 'PRIVATE_MARKER' }, 422)))
  await expect(api.getCurrentSource()).rejects.toMatchObject({ field })
  expect(api.errorFieldMessage(field)).not.toContain(field)
  expect(api.errorFieldMessage(field)).not.toContain('PRIVATE_MARKER')
})
it('rejects arbitrary field names without retaining or displaying them', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(sourceResponse({ category: 'invalid_geometry', field: 'PRIVATE_MARKER<input>' }, 422)))
  await expect(api.getCurrentSource()).rejects.toMatchObject({ field: null })
  expect(api.errorFieldMessage('PRIVATE_MARKER<input>')).toBeNull()
})
