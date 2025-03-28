import gettext
import typing
import unittest

import numpy

# local libraries
from nion.swift import Facade
from nion.data import DataAndMetadata
from nion.swift.test import TestContext
from nion.ui import TestUI
from nion.swift import Application

from nionswift_plugin.nion_experimental_tools import FindLocalMaxima

_ = gettext.gettext


Facade.initialize()


def create_memory_profile_context() -> TestContext.MemoryProfileContext:
    return TestContext.MemoryProfileContext()


class TestFindLocalMaxima(unittest.TestCase):

    def setUp(self):
        self._test_setup = TestContext.TestSetup(set_global=True)
        self._test_setup.app.workspace_dir = str()

    def tearDown(self):
        self._test_setup = typing.cast(typing.Any, None)

    def test_function_find_local_maxima_for_1D_data(self):
        data = numpy.zeros(100)
        data[[0, 8, 17, 54, 86]] = 1.0

        maxima, values = FindLocalMaxima.function_find_local_maxima(data)

        # 0 should be excluded because it is an edge-maximum
        self.assertSequenceEqual([(8,), (17,), (54,), (86,)], maxima)


    def test_function_find_local_maxima_for_2D_data(self):
        data = numpy.zeros((100, 100))
        # data[[(0, 17), (8, 8), (17, 9), (54, 40), (86, 12), (43, 99)]] = 1.0
        data[[0, 8, 17, 54, 86, 43], [17, 8, 9, 40, 12, 99]] = 1.0

        maxima, values = FindLocalMaxima.function_find_local_maxima(data)

        # 0 and 99 should be excluded because they are edge-maxima
        self.assertSequenceEqual([(8, 8), (17, 9), (54, 40), (86, 12)], maxima)