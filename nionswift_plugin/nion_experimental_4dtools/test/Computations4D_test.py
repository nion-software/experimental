# standard libraries
import time
import typing
import unittest

# third party libraries
import numpy
import scipy

# local libraries
from nion.data import Calibration
from nion.data import DataAndMetadata
from nion.data.xdata_1_0 import data_descriptor
from nion.swift import Application
from nion.swift import Facade
from nion.swift.model import DataItem
from nion.swift.model import Graphics
from nion.swift.model import PlugInManager
from nion.swift.model import Symbolic
from nion.swift.test import TestContext
from nion.ui import TestUI
from nion.utils import Geometry

from .. import CenterOfMass4D
from .. import DarkCorrection4D
from .. import FramewiseDarkCorrection
from .. import Map4D
from .. import Map4DRGB


Facade.initialize()


def create_memory_profile_context() -> TestContext.MemoryProfileContext:
    return TestContext.MemoryProfileContext()


class TestComputations4D(unittest.TestCase):

    def setUp(self) -> None:
        self._test_setup = TestContext.TestSetup(set_global=True)

    def tearDown(self) -> None:
        self._test_setup = typing.cast(typing.Any, None)

    def test_center_of_mass_4D_computation(self) -> None:
        with create_memory_profile_context() as test_context:
            document_controller = test_context.create_document_controller_with_application()
            document_model = document_controller.document_model
            xdata = DataAndMetadata.new_data_and_metadata(numpy.random.randn(4,4,4,4), data_descriptor=DataAndMetadata.DataDescriptor(False, 2, 2))
            data_item = DataItem.new_data_item(xdata)
            data_item.title = "4D Data"
            document_model.append_data_item(data_item)
            display_panel = document_controller.selected_display_panel
            display_item = document_model.get_display_item_for_data_item(data_item)
            display_panel.set_display_panel_display_item(display_item)
            api = Facade.get_api("~1.0", "~1.0")
            region1_graphic = Graphics.RectangleGraphic()
            region1_graphic.bounds = Geometry.FloatRect.from_tlhw(0.4, 0.3, 0.2, 0.4)
            display_item.add_graphic(region1_graphic)
            map_regions = [Facade.Graphic(region1_graphic)]
            map_data_item = CenterOfMass4D.center_of_mass_4D(api, Facade.DocumentWindow(document_controller), Facade.Display(display_item), map_regions)
            map_display_item = document_model.get_display_item_for_data_item(map_data_item._data_item)
            document_model.recompute_all()
            document_controller.periodic()
            self.assertEqual(2, len(document_model.data_items))
            self.assertFalse(any(computation.error_text for computation in document_model.computations))
            self.assertIn("Center of Mass", map_data_item.title)
            self.assertEqual(1, len(display_item.graphics))
            self.assertEqual(1, len(map_display_item.graphics))

    def test_dark_correction_4D_computation(self) -> None:
        with create_memory_profile_context() as test_context:
            document_controller = test_context.create_document_controller_with_application()
            document_model = document_controller.document_model
            xdata = DataAndMetadata.new_data_and_metadata(numpy.random.randn(4,4,4,4), data_descriptor=DataAndMetadata.DataDescriptor(False, 2, 2))
            data_item = DataItem.new_data_item(xdata)
            data_item.title = "4D Data"
            document_model.append_data_item(data_item)
            display_panel = document_controller.selected_display_panel
            display_item = document_model.get_display_item_for_data_item(data_item)
            display_panel.set_display_panel_display_item(display_item)
            api = Facade.get_api("~1.0", "~1.0")
            DarkCorrection4D.register_computations()
            bin_data_item, corrected_data_item = DarkCorrection4D.dark_correction_4D(api, Facade.DocumentWindow(document_controller), Facade.DataItem(data_item), 'auto', True, None)
            document_model.recompute_all()
            document_controller.periodic()
            self.assertFalse(any(computation.error_text for computation in document_model.computations))
            self.assertEqual(3, len(document_model.data_items))
            self.assertIn("Total Bin 4D", bin_data_item.title)
            self.assertIn("4D Dark Correction", corrected_data_item.title)

    def test_framewise_dark_correction_4D_computation(self) -> None:
        with create_memory_profile_context() as test_context:
            document_controller = test_context.create_document_controller_with_application()
            document_model = document_controller.document_model
            xdata = DataAndMetadata.new_data_and_metadata(numpy.random.randn(4,4,8,8), data_descriptor=DataAndMetadata.DataDescriptor(False, 2, 2))
            data_item = DataItem.new_data_item(xdata)
            data_item.title = "4D Data"
            document_model.append_data_item(data_item)
            display_panel = document_controller.selected_display_panel
            display_item = document_model.get_display_item_for_data_item(data_item)
            display_panel.set_display_panel_display_item(display_item)
            api = Facade.get_api("~1.0", "~1.0")
            FramewiseDarkCorrection.register_computations()
            average_data_item, corrected_data_item = FramewiseDarkCorrection.framewise_correction_4D(api, Facade.DocumentWindow(document_controller), Facade.DataItem(data_item), 'auto', True, None)
            document_model.recompute_all()
            document_controller.periodic()
            self.assertFalse(any(computation.error_text for computation in document_model.computations))
            self.assertEqual(3, len(document_model.data_items))
            self.assertIn("Frame Average 4D", average_data_item.title)
            self.assertIn("Framewise Dark Correction", corrected_data_item.title)

    def test_map_4D_computation(self) -> None:
        with create_memory_profile_context() as test_context:
            document_controller = test_context.create_document_controller_with_application()
            document_model = document_controller.document_model
            xdata = DataAndMetadata.new_data_and_metadata(numpy.random.randn(4,4,4,4), data_descriptor=DataAndMetadata.DataDescriptor(False, 2, 2))
            data_item = DataItem.new_data_item(xdata)
            data_item.title = "4D Data"
            document_model.append_data_item(data_item)
            display_panel = document_controller.selected_display_panel
            display_item = document_model.get_display_item_for_data_item(data_item)
            display_panel.set_display_panel_display_item(display_item)
            api = Facade.get_api("~1.0", "~1.0")
            region1_graphic = Graphics.RectangleGraphic()
            region1_graphic.bounds = Geometry.FloatRect.from_tlhw(0.4, 0.3, 0.2, 0.4)
            display_item.add_graphic(region1_graphic)
            map_regions = [Facade.Graphic(region1_graphic)]
            map_data_item = Map4D.map_4D(api, Facade.DocumentWindow(document_controller), Facade.Display(display_item), map_regions)
            map_display_item = document_model.get_display_item_for_data_item(map_data_item._data_item)
            document_model.recompute_all()
            document_controller.periodic()
            self.assertEqual(2, len(document_model.data_items))
            self.assertFalse(any(computation.error_text for computation in document_model.computations))
            self.assertIn("Map 4D", map_data_item.title)
            self.assertEqual(1, len(display_item.graphics))
            self.assertEqual(1, len(map_display_item.graphics))

    def test_map_4D_RGB_computation(self) -> None:
        with create_memory_profile_context() as test_context:
            document_controller = test_context.create_document_controller_with_application()
            document_model = document_controller.document_model
            xdata = DataAndMetadata.new_data_and_metadata(numpy.random.randn(4,4,4,4), data_descriptor=DataAndMetadata.DataDescriptor(False, 2, 2))
            data_item = DataItem.new_data_item(xdata)
            data_item.title = "4D Data"
            document_model.append_data_item(data_item)
            display_panel = document_controller.selected_display_panel
            display_item = document_model.get_display_item_for_data_item(data_item)
            display_panel.set_display_panel_display_item(display_item)
            api = Facade.get_api("~1.0", "~1.0")
            region1_graphic = Graphics.RectangleGraphic()
            region1_graphic.bounds = Geometry.FloatRect.from_tlhw(0.4, 0.3, 0.2, 0.4)
            display_item.add_graphic(region1_graphic)
            region2_graphic = Graphics.RectangleGraphic()
            region2_graphic.bounds = Geometry.FloatRect.from_tlhw(0.4, 0.3, 0.2, 0.4)
            display_item.add_graphic(region2_graphic)
            region3_graphic = Graphics.RectangleGraphic()
            region3_graphic.bounds = Geometry.FloatRect.from_tlhw(0.4, 0.3, 0.2, 0.4)
            display_item.add_graphic(region3_graphic)
            map_regions_r = [Facade.Graphic(region1_graphic)]
            map_regions_g = [Facade.Graphic(region2_graphic)]
            map_regions_b = [Facade.Graphic(region3_graphic)]
            map_data_item = Map4DRGB.map_4D_RGB(api, Facade.DocumentWindow(document_controller), Facade.Display(display_item), map_regions_r, map_regions_g, map_regions_b)
            map_display_item = document_model.get_display_item_for_data_item(map_data_item._data_item)
            document_model.recompute_all()
            document_controller.periodic()
            self.assertEqual(2, len(document_model.data_items))
            self.assertFalse(any(computation.error_text for computation in document_model.computations))
            self.assertIn("Map 4D RGB", map_data_item.title)
            self.assertEqual(3, len(display_item.graphics))
            self.assertEqual(1, len(map_display_item.graphics))
