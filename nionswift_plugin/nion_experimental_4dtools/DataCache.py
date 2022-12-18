import threading
import time
import typing
import uuid

import numpy as np

from nion.swift import Facade

_DataArrayType = np.typing.NDArray[typing.Any]


class DataCache:
    def __init__(self, lifetime: float = 30.0,
                 modify_data_fn: typing.Optional[typing.Callable[[_DataArrayType], _DataArrayType]] = None) -> None:
        self.__cached_data: typing.Optional[_DataArrayType] = None
        self.__cached_uuid: typing.Optional[str] = None
        self.__last_requested = time.time()

        self.lifetime = lifetime
        self.modify_data_fn = modify_data_fn if callable(modify_data_fn) else typing.cast(typing.Callable[[_DataArrayType], _DataArrayType], np.array)

        self.__thread = threading.Thread(target=self.__cache_loop, daemon=True)
        self.__lock = threading.Lock()
        self.__thread.start()

    def __cache_loop(self) -> None:
        while True:
            with self.__lock:
                now = time.time()
                if now - self.__last_requested > self.lifetime:
                    self.__cached_data = None
                    self.__cached_uuid = None
            time.sleep(0.5)

    def get_cached_data(self, data_source: Facade.DataItem) -> typing.Optional[_DataArrayType]:
        uuid = str(data_source.uuid)
        with self.__lock:
            if self.__cached_uuid != uuid:
                xdata = data_source.xdata
                assert xdata and xdata.data is not None
                self.__cached_uuid = uuid
                self.__cached_data = self.modify_data_fn(xdata.data)
                #self.__cached_data = np.reshape(xdata.data, xdata.data.shape[:2] + (-1,))
            self.__last_requested = time.time()
            return self.__cached_data
