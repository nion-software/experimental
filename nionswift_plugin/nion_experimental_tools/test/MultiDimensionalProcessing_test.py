import gettext
import typing
import unittest

import numpy

# local libraries
from nion.data import DataAndMetadata
from nion.swift import Application
from nion.swift import Facade
from nion.swift.model import DataItem
from nion.swift.model import Graphics
from nion.swift.test import TestContext
from nion.ui import TestUI
from nion.utils import Geometry

from .. import AlignMultiSI
from .. import AlignSequenceOfMultiDimensionalData
from .. import MakeColorCOM
from .. import MakeIDPC
from .. import DoubleGaussian
from .. import FindLocalMaxima
from .. import MultiDimensionalProcessing
from .. import SequenceSplitJoin

_ = gettext.gettext


Facade.initialize()


def create_memory_profile_context() -> TestContext.MemoryProfileContext:
    return TestContext.MemoryProfileContext()


class TestMultiDimensionalProcessing(unittest.TestCase):

    def setUp(self):
        self._test_setup = TestContext.TestSetup(set_global=True)
        self._test_setup.app.workspace_dir = str()

    def tearDown(self):
        self._test_setup = typing.cast(typing.Any, None)

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

            data = numpy.ones((5, 4, 5))

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

            # the region will cover the centers of the middle two pixels vertically x three pixels horizontally = 6.0
            self.assertTrue(numpy.allclose(integrated.data, 6.0))

    def test_align_si_computation(self) -> None:
        with create_memory_profile_context() as test_context:
            document_controller = test_context.create_document_controller_with_application()
            document_model = document_controller.document_model
            api = Facade.get_api("~1.0", "~1.0")
            # setup
            haadf_xdata = DataAndMetadata.new_data_and_metadata(numpy.random.randn(8,8,8), data_descriptor=DataAndMetadata.DataDescriptor(True, 0, 2))
            haadf_data_item = DataItem.new_data_item(haadf_xdata)
            haadf_data_item.title = "HAADF"
            document_model.append_data_item(haadf_data_item)
            si_xdata = DataAndMetadata.new_data_and_metadata(numpy.random.randn(8,8,8,16), data_descriptor=DataAndMetadata.DataDescriptor(True, 2, 1))
            si_data_item = DataItem.new_data_item(si_xdata)
            si_data_item.title = "SI"
            document_model.append_data_item(si_data_item)
            haadf_display_item = document_model.get_display_item_for_data_item(haadf_data_item)
            align_region_graphic = Graphics.RectangleGraphic()
            align_region_graphic.bounds = Geometry.FloatRect.from_tlhw(0.4, 0.3, 0.2, 0.4)
            haadf_display_item.add_graphic(align_region_graphic)
            # make computation and execute
            aligned_haadf, aligned_si = AlignMultiSI.align_multi_si(api, Facade.DocumentWindow(document_controller), Facade.DataItem(haadf_data_item), Facade.Graphic(align_region_graphic), Facade.DataItem(si_data_item), 4)
            document_model.recompute_all()
            document_controller.periodic()
            # check results
            self.assertEqual(4, len(document_model.data_items))
            self.assertFalse(any(computation.error_text for computation in document_model.computations))
            self.assertIn("(Align and Integrate SI Sequence - Aligned HAADF)", aligned_haadf.title)
            self.assertIn("(Align and Integrate SI Sequence - Aligned SI)", aligned_si.title)

    def test_align_si2_computation(self) -> None:
        with create_memory_profile_context() as test_context:
            document_controller = test_context.create_document_controller_with_application()
            document_model = document_controller.document_model
            api = Facade.get_api("~1.0", "~1.0")
            # setup
            haadf_xdata = DataAndMetadata.new_data_and_metadata(numpy.random.randn(8,8,8), data_descriptor=DataAndMetadata.DataDescriptor(True, 0, 2))
            haadf_data_item = DataItem.new_data_item(haadf_xdata)
            haadf_data_item.title = "HAADF"
            document_model.append_data_item(haadf_data_item)
            si_xdata = DataAndMetadata.new_data_and_metadata(numpy.random.randn(8,8,8,16), data_descriptor=DataAndMetadata.DataDescriptor(True, 2, 1))
            si_data_item = DataItem.new_data_item(si_xdata)
            si_data_item.title = "SI"
            document_model.append_data_item(si_data_item)
            haadf_display_item = document_model.get_display_item_for_data_item(haadf_data_item)
            align_region_graphic = Graphics.RectangleGraphic()
            align_region_graphic.bounds = Geometry.FloatRect.from_tlhw(0.4, 0.3, 0.2, 0.4)
            haadf_display_item.add_graphic(align_region_graphic)
            # make computation and execute
            aligned_haadf, aligned_si, shifts = AlignMultiSI.align_multi_si2(api, Facade.DocumentWindow(document_controller), Facade.DataItem(haadf_data_item), Facade.Graphic(align_region_graphic), Facade.DataItem(si_data_item))
            document_model.recompute_all()
            document_controller.periodic()
            # check results
            self.assertEqual(5, len(document_model.data_items))
            self.assertFalse(any(computation.error_text for computation in document_model.computations))
            self.assertIn("(Align and Integrate SI Sequence - Integrated HAADF)", aligned_haadf.title)
            self.assertIn("(Align and Integrate SI Sequence - Integrated SI)", aligned_si.title)
            self.assertIn("Measured Shifts", shifts.title)

    def test_align_sequence_of_multi_dim_data_computation(self) -> None:
        with create_memory_profile_context() as test_context:
            document_controller = test_context.create_document_controller_with_application()
            document_model = document_controller.document_model
            api = Facade.get_api("~1.0", "~1.0")
            # setup
            haadf_xdata = DataAndMetadata.new_data_and_metadata(numpy.random.randn(10,8,8), data_descriptor=DataAndMetadata.DataDescriptor(True, 0, 2))
            haadf_data_item = DataItem.new_data_item(haadf_xdata)
            haadf_data_item.title = "HAADF"
            document_model.append_data_item(haadf_data_item)
            si_xdata = DataAndMetadata.new_data_and_metadata(numpy.random.randn(10,8,8,16), data_descriptor=DataAndMetadata.DataDescriptor(True, 2, 1))
            si_data_item = DataItem.new_data_item(si_xdata)
            si_data_item.title = "SI"
            document_model.append_data_item(si_data_item)
            haadf_display_item = document_model.get_display_item_for_data_item(haadf_data_item)
            align_region_graphic = Graphics.RectangleGraphic()
            align_region_graphic.bounds = Geometry.FloatRect.from_tlhw(0.4, 0.3, 0.2, 0.4)
            haadf_display_item.add_graphic(align_region_graphic)
            # make computation and execute
            aligned_haadf, aligned_si = AlignSequenceOfMultiDimensionalData.align_multi_si(api, Facade.DocumentWindow(document_controller), Facade.DataItem(haadf_data_item), Facade.DataItem(si_data_item))
            document_model.recompute_all()
            document_controller.periodic()
            # check results
            self.assertEqual(4, len(document_model.data_items))
            self.assertFalse(any(computation.error_text for computation in document_model.computations))
            self.assertIn("Aligned", aligned_haadf.title)
            self.assertIn("Aligned", aligned_si.title)

    def test_double_gaussian_computation(self) -> None:
        with create_memory_profile_context() as test_context:
            document_controller = test_context.create_document_controller_with_application()
            document_model = document_controller.document_model
            api = Facade.get_api("~1.0", "~1.0")
            # setup
            data_item = DataItem.new_data_item(numpy.random.randn(8,8))
            data_item.title = "Input"
            document_model.append_data_item(data_item)
            # make computation and execute
            result_data_item, fft_data_item = DoubleGaussian.double_gaussian(api, Facade.DocumentWindow(document_controller), Facade.DataItem(data_item))
            document_model.recompute_all()
            document_controller.periodic()
            # check results
            self.assertEqual(3, len(document_model.data_items))
            self.assertFalse(any(computation.error_text for computation in document_model.computations))
            self.assertIn("Double Gaussian", result_data_item.title)
            self.assertIn("Filtered FFT", fft_data_item.title)

    def test_find_local_maxima_computation(self) -> None:
        with create_memory_profile_context() as test_context:
            document_controller = test_context.create_document_controller_with_application()
            document_model = document_controller.document_model
            api = Facade.get_api("~1.0", "~1.0")
            # setup
            data = numpy.zeros((8, 8))
            data[4, 4] = 10.0
            data_item = DataItem.new_data_item(data)
            data_item.title = "Input"
            document_model.append_data_item(data_item)
            display_item = document_model.get_display_item_for_data_item(data_item)
            # make computation and execute
            FindLocalMaxima.find_local_maxima(api, Facade.DataItem(data_item))
            document_model.recompute_all()
            document_controller.periodic()
            # check results
            self.assertEqual(1, len(document_model.data_items))
            self.assertFalse(any(computation.error_text for computation in document_model.computations))
            self.assertEqual(1, len(display_item.graphics))

    def test_color_COM_computation(self) -> None:
        with create_memory_profile_context() as test_context:
            document_controller = test_context.create_document_controller_with_application()
            document_model = document_controller.document_model
            api = Facade.get_api("~1.0", "~1.0")
            # setup
            xdata = DataAndMetadata.new_data_and_metadata(numpy.random.randn(8,8,8), data_descriptor=DataAndMetadata.DataDescriptor(True, 0, 2))
            data_item = DataItem.new_data_item(xdata)
            data_item.title = "HAADF"
            document_model.append_data_item(data_item)
            # make computation and execute
            color_com_data_item, divergence_data_item = MakeColorCOM.color_COM(api, Facade.DocumentWindow(document_controller), Facade.DataItem(data_item))
            document_model.recompute_all()
            document_controller.periodic()
            # check results
            self.assertEqual(3, len(document_model.data_items))
            self.assertFalse(any(computation.error_text for computation in document_model.computations))
            self.assertIn("Color COM", color_com_data_item.title)
            self.assertIn("Divergence", divergence_data_item.title)

    def test_iDPC_computation(self) -> None:
        with create_memory_profile_context() as test_context:
            document_controller = test_context.create_document_controller_with_application()
            document_model = document_controller.document_model
            api = Facade.get_api("~1.0", "~1.0")
            # setup
            xdata = DataAndMetadata.new_data_and_metadata(numpy.random.randn(8,8,8), data_descriptor=DataAndMetadata.DataDescriptor(True, 0, 2))
            data_item = DataItem.new_data_item(xdata)
            data_item.title = "HAADF"
            document_model.append_data_item(data_item)
            # make computation and execute
            idpc_data_item = MakeIDPC.iDPC(api, Facade.DocumentWindow(document_controller), Facade.DataItem(data_item))
            document_model.recompute_all()
            document_controller.periodic()
            # check results
            self.assertEqual(2, len(document_model.data_items))
            self.assertFalse(any(computation.error_text for computation in document_model.computations))
            self.assertIn("iDPC", idpc_data_item.title)

    def test_measure_shifts_computation_and_apply_shifts_computation(self) -> None:
        with self.subTest(msg="Test for a sequence of 2D data. Measure shifts in data axis."):
            with create_memory_profile_context() as test_context:
                document_controller = test_context.create_document_controller_with_application()
                document_model = document_controller.document_model
                api = Facade.get_api("~1.0", "~1.0")
                # setup
                xdata = DataAndMetadata.new_data_and_metadata(numpy.random.randn(8,8,8), data_descriptor=DataAndMetadata.DataDescriptor(True, 0, 2))
                data_item = DataItem.new_data_item(xdata)
                data_item.title = "HAADF"
                document_model.append_data_item(data_item)
                # make computation and execute
                shifts_data_item = MultiDimensionalProcessing.measure_shifts(api, Facade.DocumentWindow(document_controller), Facade.DataItem(data_item), None, "data")
                document_model.recompute_all()
                document_controller.periodic()
                # check results
                self.assertEqual(2, len(document_model.data_items))
                self.assertFalse(any(computation.error_text for computation in document_model.computations))
                self.assertIn("Measure Shifts", shifts_data_item.title)
                # make computation and execute
                shifted_data_item = MultiDimensionalProcessing.apply_shifts(api, Facade.DocumentWindow(document_controller), Facade.DataItem(data_item), shifts_data_item, "data")
                document_model.recompute_all()
                document_controller.periodic()
                self.assertEqual(3, len(document_model.data_items))
                self.assertFalse(any(computation.error_text for computation in document_model.computations))
                self.assertIn("(Apply Shifts)", shifted_data_item.title)

        with self.subTest(msg="Test for a 2D collection of 2D data. Measure shifts in data axis."):
            with create_memory_profile_context() as test_context:
                document_controller = test_context.create_document_controller_with_application()
                document_model = document_controller.document_model
                api = Facade.get_api("~1.0", "~1.0")
                # setup
                xdata = DataAndMetadata.new_data_and_metadata(numpy.random.randn(7,8,9,10), data_descriptor=DataAndMetadata.DataDescriptor(False, 2, 2))
                data_item = DataItem.new_data_item(xdata)
                data_item.title = "HAADF"
                document_model.append_data_item(data_item)
                # make computation and execute
                shifts_data_item = MultiDimensionalProcessing.measure_shifts(api, Facade.DocumentWindow(document_controller), Facade.DataItem(data_item), None, "data")
                document_model.recompute_all()
                document_controller.periodic()
                # check results
                self.assertEqual(2, len(document_model.data_items))
                self.assertFalse(any(computation.error_text for computation in document_model.computations))
                self.assertIn("Measure Shifts", shifts_data_item.title)
                # make computation and execute
                shifted_data_item = MultiDimensionalProcessing.apply_shifts(api, Facade.DocumentWindow(document_controller), Facade.DataItem(data_item), shifts_data_item, "data")
                document_model.recompute_all()
                document_controller.periodic()
                self.assertEqual(3, len(document_model.data_items))
                self.assertFalse(any(computation.error_text for computation in document_model.computations))
                self.assertIn("(Apply Shifts)", shifted_data_item.title)

    def test_crop_multidimensional_computation(self) -> None:
        with create_memory_profile_context() as test_context:
            document_controller = test_context.create_document_controller_with_application()
            document_model = document_controller.document_model
            api = Facade.get_api("~1.0", "~1.0")
            # setup
            xdata = DataAndMetadata.new_data_and_metadata(numpy.random.randn(8,8,8), data_descriptor=DataAndMetadata.DataDescriptor(True, 0, 2))
            data_item = DataItem.new_data_item(xdata)
            data_item.title = "HAADF"
            document_model.append_data_item(data_item)
            # make computation and execute
            crop_data_item = MultiDimensionalProcessing.crop_multi_dimensional(api, Facade.DocumentWindow(document_controller), Facade.DataItem(data_item), None, "data")
            document_model.recompute_all()
            document_controller.periodic()
            # check results
            self.assertEqual(2, len(document_model.data_items))
            self.assertFalse(any(computation.error_text for computation in document_model.computations))
            self.assertIn("Multidimensional Crop", crop_data_item.title)

    def test_tableau_computation(self) -> None:
        with create_memory_profile_context() as test_context:
            document_controller = test_context.create_document_controller_with_application()
            document_model = document_controller.document_model
            api = Facade.get_api("~1.0", "~1.0")
            # setup
            xdata = DataAndMetadata.new_data_and_metadata(numpy.random.randn(8,8,8), data_descriptor=DataAndMetadata.DataDescriptor(True, 0, 2))
            data_item = DataItem.new_data_item(xdata)
            data_item.title = "HAADF"
            document_model.append_data_item(data_item)
            # make computation and execute
            result_data_item = MultiDimensionalProcessing.tableau(api, Facade.DocumentWindow(document_controller), Facade.DataItem(data_item), 1.0)
            document_model.recompute_all()
            document_controller.periodic()
            # check results
            self.assertEqual(2, len(document_model.data_items))
            self.assertFalse(any(computation.error_text for computation in document_model.computations))
            self.assertIn("Tableau", result_data_item.title)

    def test_align_image_sequence_computation(self) -> None:
        with create_memory_profile_context() as test_context:
            document_controller = test_context.create_document_controller_with_application()
            document_model = document_controller.document_model
            api = Facade.get_api("~1.0", "~1.0")
            # setup
            xdata = DataAndMetadata.new_data_and_metadata(numpy.random.randn(8,8,8), data_descriptor=DataAndMetadata.DataDescriptor(True, 0, 2))
            data_item = DataItem.new_data_item(xdata)
            data_item.title = "HAADF"
            document_model.append_data_item(data_item)
            # make computation and execute
            result_data_item, shifts, shifted_result_data_item = MultiDimensionalProcessing.align_image_sequence(api, Facade.DocumentWindow(document_controller), Facade.DataItem(data_item), 0, False, 0, False, True, None)
            document_model.recompute_all()
            document_controller.periodic()
            # check results
            self.assertEqual(3, len(document_model.data_items))
            self.assertFalse(any(computation.error_text for computation in document_model.computations))
            self.assertIn("Align/Integrate - Integrated Sequence", result_data_item.title)
            self.assertIn("Align/Integrate - Shifts", shifts.title)

    def test_sequence_join_computation(self) -> None:
        with create_memory_profile_context() as test_context:
            document_controller = test_context.create_document_controller_with_application()
            document_model = document_controller.document_model
            api = Facade.get_api("~1.0", "~1.0")
            # setup
            xdata1 = DataAndMetadata.new_data_and_metadata(numpy.random.randn(8,8,8), data_descriptor=DataAndMetadata.DataDescriptor(True, 0, 2))
            data_item1 = DataItem.new_data_item(xdata1)
            data_item1.title = "A"
            document_model.append_data_item(data_item1)
            display_item1 = document_model.get_display_item_for_data_item(data_item1)
            xdata2 = DataAndMetadata.new_data_and_metadata(numpy.random.randn(8,8,8), data_descriptor=DataAndMetadata.DataDescriptor(True, 0, 2))
            data_item2 = DataItem.new_data_item(xdata2)
            data_item2.title = "A"
            document_model.append_data_item(data_item2)
            display_item2 = document_model.get_display_item_for_data_item(data_item2)
            # make computation and execute
            result_data_item = SequenceSplitJoin.sequence_join(api, Facade.DocumentWindow(document_controller), [Facade.Display(display_item1), Facade.Display(display_item2)])
            document_model.recompute_all()
            document_controller.periodic()
            # check results
            self.assertEqual(3, len(document_model.data_items))
            self.assertFalse(any(computation.error_text for computation in document_model.computations))
            self.assertIn("(Join Sequence)", result_data_item.title)

    def test_sequence_split_computation(self) -> None:
        with create_memory_profile_context() as test_context:
            document_controller = test_context.create_document_controller_with_application()
            document_model = document_controller.document_model
            api = Facade.get_api("~1.0", "~1.0")
            # setup
            xdata = DataAndMetadata.new_data_and_metadata(numpy.random.randn(2,8,8), data_descriptor=DataAndMetadata.DataDescriptor(True, 0, 2))
            data_item = DataItem.new_data_item(xdata)
            data_item.title = "Sequence"
            document_model.append_data_item(data_item)
            display_item = document_model.get_display_item_for_data_item(data_item)
            # make computation and execute
            result_data_items = SequenceSplitJoin.sequence_split(api, Facade.DocumentWindow(document_controller), Facade.Display(display_item))
            document_model.recompute_all()
            document_controller.periodic()
            # check results
            self.assertEqual(3, len(document_model.data_items))
            self.assertFalse(any(computation.error_text for computation in document_model.computations))
            self.assertIn("Split 1/2", result_data_items[0].title)
            self.assertIn("Split 2/2", result_data_items[1].title)

            # print([computation.error_text for computation in document_model.computations])
            # print([data_item.title for data_item in document_model.data_items])
