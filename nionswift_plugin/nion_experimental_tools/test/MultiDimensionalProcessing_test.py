import gettext
import unittest

import numpy

# local libraries
from nion.swift import Facade
from nion.data import DataAndMetadata
from nion.swift.test import TestContext
from nion.ui import TestUI
from nion.swift import Application

from nionswift_plugin.nion_experimental_tools import MultiDimensionalProcessing

_ = gettext.gettext


Facade.initialize()


def create_memory_profile_context() -> TestContext.MemoryProfileContext:
    return TestContext.MemoryProfileContext()


class TestMultiDimensionalProcessing(unittest.TestCase):

    def setUp(self):
        self.app = Application.Application(TestUI.UserInterface(), set_global=True)
        self.app.workspace_dir = str()

    def tearDown(self):
        pass

    def test_function_crop_along_axis_3d(self):
        with self.subTest("Test for a sequence of 2D images. Crop sequence axis."):
            data = numpy.ones((5, 3, 4))

            data_descriptor = DataAndMetadata.DataDescriptor(True, 0, 2)
            xdata = DataAndMetadata.new_data_and_metadata(data, data_descriptor=data_descriptor)

            cropped = MultiDimensionalProcessing.function_crop_along_axis(xdata, "sequence", crop_bounds_left=1, crop_bounds_right=3)

            self.assertSequenceEqual(cropped.data_shape, (2, 3, 4))

    def test_function_crop_along_axis_4d(self):
        with self.subTest("Test for a 2D collection of 2D images. Crop data axis."):
            data = numpy.ones((5, 3, 4, 6))

            data_descriptor = DataAndMetadata.DataDescriptor(False, 2, 2)
            xdata = DataAndMetadata.new_data_and_metadata(data, data_descriptor=data_descriptor)

            cropped = MultiDimensionalProcessing.function_crop_along_axis(xdata, "data", crop_bounds_left=3, crop_bounds_right=6, crop_bounds_top=1, crop_bounds_bottom=3)

            self.assertSequenceEqual(cropped.data_shape, (5, 3, 2, 3))

    def test_apply_shifts_guesses_correct_shift_axis(self) -> None:
        with self.subTest("Test sequence of SIs, shift sequence dimension along collection axis."):
            data = numpy.zeros((5, 3, 3, 7))
            shifts = numpy.zeros((5, 2))

            data_descriptor = DataAndMetadata.DataDescriptor(True, 2, 1)
            xdata = DataAndMetadata.new_data_and_metadata(data, data_descriptor=data_descriptor)
            shifts_xdata = DataAndMetadata.new_data_and_metadata(shifts)

            shift_axis = MultiDimensionalProcessing.ApplyShifts.guess_starting_axis(xdata, shifts_xdata=shifts_xdata)

            self.assertEqual(shift_axis, "collection")

        with self.subTest("Test sequence of SIs, shift collection dimension along data axis."):
            data = numpy.zeros((5, 3, 3, 7))
            shifts = numpy.zeros((3, 3))

            data_descriptor = DataAndMetadata.DataDescriptor(True, 2, 1)
            xdata = DataAndMetadata.new_data_and_metadata(data, data_descriptor=data_descriptor)
            shifts_xdata = DataAndMetadata.new_data_and_metadata(shifts)

            shift_axis = MultiDimensionalProcessing.ApplyShifts.guess_starting_axis(xdata, shifts_xdata=shifts_xdata)

            self.assertEqual(shift_axis, "data")

        with self.subTest("Test sequence of SIs, shift sequence dimension along data axis."):
            data = numpy.zeros((5, 3, 3, 7))
            shifts = numpy.zeros((5,))

            data_descriptor = DataAndMetadata.DataDescriptor(True, 2, 1)
            xdata = DataAndMetadata.new_data_and_metadata(data, data_descriptor=data_descriptor)
            shifts_xdata = DataAndMetadata.new_data_and_metadata(shifts)

            shift_axis = MultiDimensionalProcessing.ApplyShifts.guess_starting_axis(xdata, shifts_xdata=shifts_xdata)

            self.assertEqual(shift_axis, "data")

        with self.subTest("Test sequence of 4D Data, shift sequence dimension along collection axis."):
            data = numpy.zeros((5, 3, 3, 4, 7))
            shifts = numpy.zeros((5, 2))

            data_descriptor = DataAndMetadata.DataDescriptor(True, 2, 2)
            xdata = DataAndMetadata.new_data_and_metadata(data, data_descriptor=data_descriptor)
            shifts_xdata = DataAndMetadata.new_data_and_metadata(shifts)

            shift_axis = MultiDimensionalProcessing.ApplyShifts.guess_starting_axis(xdata, shifts_xdata=shifts_xdata)

            self.assertEqual(shift_axis, "collection")

        with self.subTest("Test sequence of 4D Data, shift collection dimension along data axis."):
            data = numpy.zeros((5, 3, 3, 4, 7))
            shifts = numpy.zeros((3, 3, 2))

            data_descriptor = DataAndMetadata.DataDescriptor(True, 2, 2)
            xdata = DataAndMetadata.new_data_and_metadata(data, data_descriptor=data_descriptor)
            shifts_xdata = DataAndMetadata.new_data_and_metadata(shifts)

            shift_axis = MultiDimensionalProcessing.ApplyShifts.guess_starting_axis(xdata, shifts_xdata=shifts_xdata)

            self.assertEqual(shift_axis, "data")

        with self.subTest("Test sequence of 2D images, shift sequence dimension along data axis."):
            data = numpy.zeros((5, 3, 3))
            shifts = numpy.zeros((5, 2))

            data_descriptor = DataAndMetadata.DataDescriptor(True, 0, 2)
            xdata = DataAndMetadata.new_data_and_metadata(data, data_descriptor=data_descriptor)
            shifts_xdata = DataAndMetadata.new_data_and_metadata(shifts)

            shift_axis = MultiDimensionalProcessing.ApplyShifts.guess_starting_axis(xdata, shifts_xdata=shifts_xdata)

            self.assertEqual(shift_axis, "data")

        with self.subTest("Test SI, shift collection dimension along data axis."):
            data = numpy.zeros((5, 5, 3))
            shifts = numpy.zeros((5, 5))

            data_descriptor = DataAndMetadata.DataDescriptor(False, 2, 1)
            xdata = DataAndMetadata.new_data_and_metadata(data, data_descriptor=data_descriptor)
            shifts_xdata = DataAndMetadata.new_data_and_metadata(shifts)

            shift_axis = MultiDimensionalProcessing.ApplyShifts.guess_starting_axis(xdata, shifts_xdata=shifts_xdata)

            self.assertEqual(shift_axis, "data")

    def test_integrate_along_axis_menu_item(self):
        with create_memory_profile_context() as test_context:
            document_controller = test_context.create_document_controller_with_application()
            api = Facade.get_api("~1.0", "~1.0")
            document_model = document_controller.document_model

            data = numpy.ones((5, 3, 4))

            data_descriptor = DataAndMetadata.DataDescriptor(True, 0, 2)
            xdata = DataAndMetadata.new_data_and_metadata(data, data_descriptor=data_descriptor)

            source_data_item = api.library.create_data_item_from_data_and_metadata(xdata)
            document_controller.selected_display_panel = None
            document_controller.selection.set(0)

            menu_item_delegate = MultiDimensionalProcessing.IntegrateAlongAxisMenuItemDelegate(api)
            menu_item_delegate.menu_item_execute(api.application.document_windows[0])

            document_model.recompute_all()

            self.assertEqual(len(document_model.data_items), 2)
            integrated = document_model.data_items[1].xdata
            self.assertSequenceEqual(integrated.data_shape, (3, 4))
            self.assertTrue(numpy.allclose(integrated.data, 5.0))

    def test_integrate_along_axis_menu_item_with_graphic(self):
        with create_memory_profile_context() as test_context:
            document_controller = test_context.create_document_controller_with_application()
            api = Facade.get_api("~1.0", "~1.0")
            document_model = document_controller.document_model

            data = numpy.ones((5, 3, 4))

            data_descriptor = DataAndMetadata.DataDescriptor(True, 0, 2)
            xdata = DataAndMetadata.new_data_and_metadata(data, data_descriptor=data_descriptor)

            source_data_item = api.library.create_data_item_from_data_and_metadata(xdata)
            source_data_item.add_rectangle_region(0.5, 0.5, 0.5, 0.5)
            document_controller.selected_display_panel = None
            document_controller.selection.set(0)
            source_data_item.display._display_item.graphic_selection.set(0)

            menu_item_delegate = MultiDimensionalProcessing.IntegrateAlongAxisMenuItemDelegate(api)
            menu_item_delegate.menu_item_execute(api.application.document_windows[0])

            document_model.recompute_all()

            self.assertEqual(len(document_model.data_items), 2)
            integrated = document_model.data_items[1].xdata
            self.assertSequenceEqual(integrated.data_shape, (5,))
            self.assertTrue(numpy.allclose(integrated.data, 6.0))
