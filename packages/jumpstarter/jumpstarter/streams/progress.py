import logging
from dataclasses import dataclass, field

from anyio.abc import ObjectStream
from tqdm import tqdm

TQDM_KWARGS = {
    "unit": "B",
    "unit_scale": True,
    "unit_divisor": 1024,
}

LOGGER = logging.getLogger(__name__)


# Copied from https://github.com/tqdm/tqdm/pull/1172
class logging_tqdm(tqdm):
    """
    A version of tqdm that outputs the progress bar
    to Python logging instead of the console.
    The progress will be logged with the info level.
    Parameters
    ----------
    logger   : logging.Logger, optional
      Which logger to output to (default: logger.getLogger('tqdm.contrib.logging')).
    All other parameters are passed on to regular tqdm,
    with the following changed default:
    mininterval: 1
    bar_format: '{desc}{percentage:3.0f}%{r_bar}'
    desc: 'progress: '
    Example
    -------
    ```python
    import logging
    from time import sleep
    from tqdm.contrib.logging import logging_tqdm
    LOG = logging.getLogger(__name__)
    if __name__ == '__main__':
        logging.basicConfig(level=logging.INFO)
        for _ in logging_tqdm(range(10), mininterval=1, logger=LOG):
            sleep(0.3)  # assume processing one item takes less than mininterval
    ```
    """

    def __init__(
        self,
        *args,
        # logger=None,  # type: logging.Logger
        # mininterval=1,  # type: float
        # bar_format='{desc}{percentage:3.0f}%{r_bar}',  # type: str
        # desc='progress: ',  # type: str
        **kwargs,
    ):
        if len(args) >= 2:
            # Note: Due to Python 2 compatibility, we can't declare additional
            #   keyword arguments in the signature.
            #   As a result, we could get (due to the defaults below):
            #     TypeError: __init__() got multiple values for argument 'desc'
            #   This will raise a more descriptive error message.
            #   Calling dummy init to avoid attribute errors when __del__ is called
            super(logging_tqdm, self).__init__([], disable=True)
            raise ValueError("only iterable may be used as a positional argument")
        tqdm_kwargs = kwargs.copy()
        self._logger = tqdm_kwargs.pop("logger", None)
        tqdm_kwargs.setdefault("mininterval", 1)
        tqdm_kwargs.setdefault("bar_format", "{desc}{percentage:3.0f}%{r_bar}")
        tqdm_kwargs.setdefault("desc", "progress: ")
        self._last_log_n = -1
        super(logging_tqdm, self).__init__(*args, **tqdm_kwargs)

    def _get_logger(self):
        if self._logger is not None:
            return self._logger
        return LOGGER

    def display(self, msg=None, pos=None):
        if not self.n:
            # skip progress bar before having processed anything
            LOGGER.debug("ignoring message before any progress: %r", self.n)
            return
        if self.n == self._last_log_n:
            # avoid logging for the same progress multiple times
            LOGGER.debug("ignoring log message with same n: %r", self.n)
            return
        self._last_log_n = self.n
        if msg is None:
            msg = self.__str__()
        if not msg:
            LOGGER.debug("ignoring empty message: %r", msg)
            return
        self._get_logger().info("%s", msg)


@dataclass(kw_only=True)
class ProgressStream(ObjectStream[bytes]):
    stream: ObjectStream
    logging: bool = False

    __recv: tqdm = field(init=False, default=None)
    __send: tqdm = field(init=False, default=None)

    def __del__(self):
        if self.__recv is not None:
            self.__recv.close()
        if self.__send is not None:
            self.__send.close()

    async def receive(self):
        item = await self.stream.receive()

        if self.__recv is None:
            if self.logging:
                self.__recv = logging_tqdm(desc="transfer", **TQDM_KWARGS)
            else:
                self.__recv = tqdm(desc="transfer", **TQDM_KWARGS)

        self.__recv.update(len(item))

        return item

    async def send(self, item):
        if self.__send is None:
            if self.logging:
                self.__send = logging_tqdm(desc="transfer", **TQDM_KWARGS)
            else:
                self.__send = tqdm(desc="transfer", **TQDM_KWARGS)

        self.__send.update(len(item))

        await self.stream.send(item)

    async def send_eof(self):
        await self.stream.send_eof()

    async def aclose(self):
        await self.stream.aclose()
