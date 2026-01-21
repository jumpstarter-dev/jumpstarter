import logging
from collections import deque

from jumpstarter_protocol import jumpstarter_pb2


class LogHandler(logging.Handler):
    def __init__(self, queue: deque):
        logging.Handler.__init__(self)
        self.queue = queue
        self.listener = None

    def enqueue(self, record):
        self.queue.append(record)

    def prepare(self, record):
        return jumpstarter_pb2.LogStreamResponse(
            uuid="",
            severity=record.levelname,
            message=self.format(record),
        )

    def emit(self, record):
        try:
            self.enqueue(self.prepare(record))
        except Exception:
            self.handleError(record)
