import {cleanup,fireEvent,render,screen} from '@testing-library/react'
import {afterEach,it,expect} from 'vitest'
import {VideoPreview} from './VideoPreview'
import {VideoState} from '../api/videoApi'
afterEach(cleanup)
it('a new session ref replaces MJPEG even without an IDLE render',()=>{const r=render(<VideoPreview state={VideoState.RUNNING} sessionKey="a"/>);const a=screen.getByRole('img').getAttribute('src');r.rerender(<VideoPreview state={VideoState.RUNNING} sessionKey="b"/>);expect(screen.getByRole('img').getAttribute('src')).not.toBe(a);fireEvent.error(screen.getByRole('img'));screen.getByText('Preview tidak tersedia');r.rerender(<VideoPreview state={VideoState.IDLE} sessionKey={null}/>);expect(screen.queryByRole('img')).toBeNull()})
