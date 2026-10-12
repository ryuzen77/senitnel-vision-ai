import {cleanup,fireEvent,render,screen} from '@testing-library/react'
import {afterEach,it,expect,vi} from 'vitest'
import {ConfigurationOverlay} from './ConfigurationOverlay'
import {validConfiguration} from '../testUtils/appFetchStub'
afterEach(cleanup)
it('keyboard moves a real-image pixel and invalidates inside confirmation',()=>{const onChange=vi.fn();render(<ConfigurationOverlay configuration={validConfiguration} disabled={false} onChange={onChange} tool="line"/>);fireEvent.keyDown(screen.getByRole('button',{name:/Titik P1/}),{key:'ArrowRight'});const c=onChange.mock.calls[0][0];expect(c.line.p1[0]).toBeCloseTo(.2+1/640);expect(c.inside).toBe('')})
it('ignores keyboard edits while disabled',()=>{const onChange=vi.fn();render(<ConfigurationOverlay configuration={validConfiguration} disabled onChange={onChange} tool="zone"/>);fireEvent.keyDown(screen.getByRole('button',{name:/Titik P1/}),{key:'ArrowRight'});expect(onChange).not.toHaveBeenCalled()})
