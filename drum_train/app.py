import asyncio
from time import time
from typing import Callable, Iterable, List, Tuple

import anyio
import asyncclick as click
import rtmidi
import tornado.queues
import tornado.web

from .config import *


drum = rtmidi.MidiIn()
drum_player = rtmidi.MidiOut()

DRUM_MSG = Tuple[int, int, int]     # time stamp, instrument, velocity


class DataHandler(tornado.web.RequestHandler):
    """Get current data from drumming queue.
    If queue is empty, wait for drum event.
    Else return all the events.
    """

    def initialize(self, queue: asyncio.LifoQueue[DRUM_MSG], **kwargs) -> None:
        self.queue = queue
        return super().initialize()

    async def get_raw(self) -> Iterable[DRUM_MSG]:
        if not self.queue.empty():
            retn = []
            try:
                while True:
                    retn.append(self.queue.get_nowait())
            except asyncio.QueueEmpty:
                return retn
        x = await self.queue.get()
        return [x]

    async def get(self):
        beats = await self.get_raw()
        self.write(dict(beats=beats))


def make_web_app(**kwargs):
    click.echo("Serving drumming data at /data")

    return tornado.web.Application([
        (r"/data", DataHandler, kwargs)
    ])


async def virtual_midi(port: int):
    click.echo("Sending midi messages")
    drum_player.open_port(port)
    with drum_player:
        while True:
            note_on = [0x90, 60, 112]  # channel 1, middle C, velocity 112
            note_off = [0x80, 60, 0]
            drum_player.send_message(note_on)
            asyncio.sleep(.1)
            drum_player.send_message(note_off)
            asyncio.sleep(1.)

MIDI_MSG = List[int]
MIDI_MSG_FULL = Tuple[MIDI_MSG, float]


async def midi_read(port, callback: Callable[[MIDI_MSG_FULL], None], **kwargs):
    drum.open_port(port)

    with drum:
        click.echo(f"Reading MIDI port {port}")
        while True:
            x: MIDI_MSG_FULL
            if x := drum.get_message():
                callback(x[0])
            await asyncio.sleep(MIDI_POLL_S)


@click.group()
@click.option("--midi-port", default=0, type=int)
@click.option("-v", "--verbose", count=True)
@click.option("--virtual", default=False)
@click.pass_context
async def cli(ctx, midi_port, virtual, verbose):

    ctx.ensure_object(dict)

    bqueue: asyncio.LifoQueue[DRUM_MSG] = \
        asyncio.LifoQueue(maxsize=BEAT_QUEUE_SZ)

    def on_beat(msg: MIDI_MSG):
        if verbose > 1:
            click.echo(msg)

        if bqueue.full():
            bqueue.get_nowait()
        bqueue.put_nowait((int(time()), msg[1], msg[2]))

    ctx.obj['virtual'] = virtual
    ctx.obj['port'] = midi_port
    ctx.obj['callback'] = on_beat
    ctx.obj['queue'] = bqueue


@cli.command()
@click.pass_context
async def console(ctx):
    await main(**ctx.obj)


@cli.command()
@click.option("--port", default=DEFAULT_SERVE_PORT, type=int)
@click.pass_context
async def web(ctx, port: int):
    app = make_web_app(queue=ctx.obj['queue'])
    while True:
        try:
            app.listen(port)
            click.echo(f"Listening on {port}")
        except OSError:
            port += 1
        else:
            break

    await main(**ctx.obj)


def block_console():
    click.echo("To quit: q↵ ")
    while True:
        if input().lower() == 'q':
            return


async def main(port, virtual, **kwargs):

    aws = [asyncio.to_thread(block_console), midi_read(port=port, **kwargs)]
    if virtual:
        aws.append(virtual_midi(port))

    await asyncio.wait(
        map(asyncio.create_task, aws),
        return_when=asyncio.FIRST_COMPLETED
    )

if __name__ == "__main__":
    cli(_anyio_backend='asyncio', obj={})
