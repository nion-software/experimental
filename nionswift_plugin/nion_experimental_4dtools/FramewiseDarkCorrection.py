# system imports
import copy
import functools
import gettext
import typing
import uuid

import numpy as np

from nion.data import DataAndMetadata
from nion.data import xdata_1_0 as xd
from nion.swift import Facade
from nion.swift.ComputationPanel import make_image_chooser
from nion.swift.model import DataItem
from nion.swift.model import DisplayItem
from nion.swift.model import Symbolic
from nion.ui import UserInterface

# local libraries

_ = gettext.gettext


class CalculateAverage4D:
    label = _("Frame Average 4D")

    def __init__(self, computation: Facade.Computation, **kwargs: typing.Any) -> None:
        self.computation = computation

    def execute(self, src: typing.Optional[Facade.DataItem] = None, **kwargs: typing.Any) -> None:
        assert src
        assert src.xdata
        self.__new_xdata = xd.sum(src.xdata, axis=(0, 1))

    def commit(self) -> None:
        self.computation.set_referenced_xdata('target', self.__new_xdata)


class FramewiseDarkCorrection:
    label = _("Framewise Dark Correction")

    def __init__(self, computation: Facade.Computation, **kwargs: typing.Any) -> None:
        self.__api = getattr(computation, "api")
        self.computation = computation

        def create_panel_widget(ui: Facade.UserInterface, document_controller: Facade.DocumentWindow) -> Facade.ColumnWidget:
            def gain_mode_changed(current_item: typing.Any) -> None:
                variable = self.computation._computation._get_variable('gain_mode')
                assert variable
                if current_item != variable.value:
                    variable.value = current_item

            def bin_data_changed(check_state: str) -> None:
                variable = self.computation._computation._get_variable('bin_spectrum')
                assert variable
                if variable.value != (check_state == 'checked'):
                    variable.value = check_state == 'checked'

            # this does nothing since objects_model is not defined. disabling. CM 2022-12-17.
            # def clear_gain_image() -> None:
            #     variable = self.computation._computation._get_variable('gain_image')
            #     if variable.objects_model.items:
            #         variable.objects_model.remove_item(0)

            column = ui.create_column_widget()
            gain_image_variable = self.computation._computation._get_variable('gain_image')
            assert gain_image_variable
            image_chooser, image_changed_listener = make_image_chooser(document_controller._document_controller,
                                                                       self.computation._computation,
                                                                       gain_image_variable,
                                                                       column._widget.drag)
            self.__image_changed_listener = image_changed_listener

            clear_gain_image_button = ui.create_push_button_widget('Clear')

            gain_mode_label = ui.create_label_widget('Gain correction mode: ')
            gain_mode_chooser = ui.create_combo_box_widget()
            gain_mode_chooser.items = ['auto', 'custom', 'off']
            bin_data_checkbox = ui.create_check_box_widget('Bin data to 1D')

            gain_mode_row = ui.create_row_widget()
            gain_mode_row.add(gain_mode_label)
            gain_mode_row.add(gain_mode_chooser)
            gain_mode_row.add_stretch()

            gain_mode_row2 = ui.create_row_widget()
            gain_mode_row2.add_stretch()
            typing.cast(UserInterface.BoxWidget, gain_mode_row2._widget).add(image_chooser)
            gain_mode_row2.add_spacing(5)
            gain_mode_row2.add(clear_gain_image_button)

            bin_data_row = ui.create_row_widget()
            bin_data_row.add(bin_data_checkbox)
            bin_data_row.add_stretch()

            column.add(gain_mode_row)
            column.add_spacing(10)
            column.add(gain_mode_row2)
            column.add_spacing(10)
            column.add(bin_data_row)
            column.add_stretch()

            gain_mode_variable = self.computation._computation._get_variable('gain_mode')
            bin_spectrum_variable = self.computation._computation._get_variable('bin_spectrum')

            assert gain_mode_variable
            assert bin_spectrum_variable

            gain_mode_chooser.current_item = gain_mode_variable.value
            bin_data_checkbox.checked = bin_spectrum_variable.value

            # see note above
            # clear_gain_image_button.on_clicked = clear_gain_image
            gain_mode_chooser.on_current_item_changed = gain_mode_changed
            bin_data_checkbox.on_check_state_changed = bin_data_changed

            return column

        typing.cast(typing.Any, self.computation._computation).create_panel_widget = create_panel_widget

    def execute(self,
                src1: typing.Optional[Facade.DataItem] = None,
                src2: typing.Optional[Facade.DataItem] = None,
                spectrum_region: typing.Optional[Facade.Graphic] = None,
                top_dark_region: typing.Optional[Facade.Graphic] = None,
                bottom_dark_region: typing.Optional[Facade.Graphic] = None,
                bin_spectrum: bool = False,
                gain_image: typing.Optional[typing.Sequence[typing.Any]] = None,  # no idea what this should be
                gain_mode: typing.Optional[str] = None,
                **kwargs: typing.Any) -> None:
        assert src1
        assert src2
        assert spectrum_region
        assert top_dark_region
        assert bottom_dark_region
        assert gain_image is not None
        assert gain_mode
        src1_xdata = src1.xdata
        src2_xdata = src2.xdata
        assert src1_xdata
        assert src2_xdata
        assert src1_xdata.metadata is not None
        data = src1_xdata.data
        assert data is not None
        data_shape = np.array(data.shape)
        metadata = dict(src1_xdata.metadata).copy()
        sensor_readout_area = metadata.get('hardware_source', {}).get('sensor_readout_area')
        sensor_dimensions = metadata.get('hardware_source', {}).get('sensor_dimensions')
        if sensor_readout_area and sensor_dimensions:
            cam_center = sensor_dimensions['height']/2 - sensor_readout_area['top']
        else:
            cam_center = int(round(data_shape[-2]/2))
        spectrum_area = np.rint(np.array(spectrum_region.bounds) * data_shape[2:]).astype(np.int_)
        top_dark_area = np.rint(np.array(top_dark_region.bounds) * data_shape[2:]).astype(np.int_)
        bottom_dark_area = np.rint(np.array(bottom_dark_region.bounds) * data_shape[2:]).astype(np.int_)
        spectrum_range_y = np.array((spectrum_area[0,0], spectrum_area[0,0] + spectrum_area[1,0]))
        spectrum_range_x = np.array((spectrum_area[0,1], spectrum_area[0,1] + spectrum_area[1,1]))
        top_dark_area_range_y = np.array((top_dark_area[0,0], top_dark_area[0,0] + top_dark_area[1, 0]))
        bottom_dark_area_range_y = np.array((bottom_dark_area[0,0], bottom_dark_area[0,0] + bottom_dark_area[1, 0]))
        # undo gain correction if neccessary
        current_gain_image_uuid = metadata.get('hardware_source', {}).get('current_gain_image')
        current_gain_image: typing.Optional[Facade.DataItem] = None
        if current_gain_image_uuid:
            current_gain_image = self.__api.library.get_data_item_by_uuid(uuid.UUID(current_gain_image_uuid))
        if metadata.get('hardware_source', {}).get('is_gain_corrected') and current_gain_image:
            assert current_gain_image.xdata
            if current_gain_image.xdata.data_shape == src1_xdata.data_shape[2:]:
                data = data/current_gain_image.xdata.data

        if (cam_center >= spectrum_range_y).all(): # spectrum is above center
            dark_image = np.mean(data[..., top_dark_area_range_y[0]:top_dark_area_range_y[1],
                                      spectrum_range_x[0]:spectrum_range_x[1]], axis=-2, keepdims=True)
            corrected_image = (data[..., spectrum_range_y[0]:spectrum_range_y[1], spectrum_range_x[0]:spectrum_range_x[1]] -
                               np.repeat(dark_image, spectrum_range_y[1]-spectrum_range_y[0], axis=-2))
        elif (cam_center <= spectrum_range_y).all(): # spectrum is below center
            dark_image = np.mean(data[..., bottom_dark_area_range_y[0]:bottom_dark_area_range_y[1],
                                      spectrum_range_x[0]:spectrum_range_x[1]], axis=-2, keepdims=True)
            corrected_image = (data[..., spectrum_range_y[0]:spectrum_range_y[1], spectrum_range_x[0]:spectrum_range_x[1]] -
                               np.repeat(dark_image, spectrum_range_y[1]-spectrum_range_y[0], axis=-2))
        else: # spectrum is on top of center
            dark_image = np.mean(data[..., top_dark_area_range_y[0]:top_dark_area_range_y[1],
                                      spectrum_range_x[0]:spectrum_range_x[1]], axis=-2, keepdims=True)
            corrected_image_top = (data[..., spectrum_range_y[0]:cam_center, spectrum_range_x[0]:spectrum_range_x[1]] -
                                   np.repeat(dark_image, cam_center-spectrum_range_y[0], axis=-2))
            dark_image = np.mean(data[..., bottom_dark_area_range_y[0]:bottom_dark_area_range_y[1],
                                      spectrum_range_x[0]:spectrum_range_x[1]], axis=-2, keepdims=True)
            corrected_image_bot = (data[..., cam_center:spectrum_range_y[1], spectrum_range_x[0]:spectrum_range_x[1]] -
                                   np.repeat(dark_image, spectrum_range_y[1]-cam_center, axis=-2))
            corrected_image = np.concatenate((corrected_image_top, corrected_image_bot), axis=-2)
            del corrected_image_top
            del corrected_image_bot
        del data
        del dark_image # don't hold references to unused objects so that garbage collector can free the memory

        if ((gain_mode == 'auto' and current_gain_image) or # apply gain correction if needed
            (gain_mode == 'custom' and gain_image)):
            assert current_gain_image
            assert current_gain_image.xdata
            gain_xdata = gain_image[0].xdata if gain_mode == 'custom' else current_gain_image.xdata
            assert gain_xdata

            if gain_xdata.data_shape == corrected_image.shape[2:]:
                corrected_image *= gain_xdata.data
            elif gain_xdata.data_shape == src1_xdata.data_shape[2:]:
                corrected_image *= gain_xdata.data[spectrum_range_y[0]:spectrum_range_y[1],
                                                   spectrum_range_x[0]:spectrum_range_x[1]]
            else:
                raise ValueError('Shape of gain image has to match last two dimensions of input data.')
            del gain_xdata

        dimensional_calibrations = copy.deepcopy(list(src1_xdata.dimensional_calibrations))
        if bin_spectrum:
            corrected_image = np.sum(corrected_image, axis=-2)
            dimensional_calibrations = dimensional_calibrations[:2] + dimensional_calibrations[3:]

        data_descriptor = DataAndMetadata.DataDescriptor(False, 2, 1 if bin_spectrum else 2)

        self.__new_xdata = DataAndMetadata.new_data_and_metadata(corrected_image,
                                                                 intensity_calibration=src1_xdata.intensity_calibration,
                                                                 dimensional_calibrations=dimensional_calibrations,
                                                                 data_descriptor=data_descriptor)

    def commit(self) -> None:
        self.computation.set_referenced_xdata('target', self.__new_xdata)

class FramewiseDarkMenuItem:

    menu_id = "4d_tools_menu"  # required, specify menu_id where this item will go
    menu_name = _("4D Tools") # optional, specify default name if not a standard menu
    menu_before_id = "window_menu" # optional, specify before menu_id if not a standard menu
    menu_item_name = _("Framewise Dark Correction")  # menu item name

    #DocumentModel.DocumentModel.register_processing_descriptions(correct_dark_processing_descriptions)
    #DocumentModel.DocumentModel.register_processing_descriptions(calculate_average_processing_descriptions)
    def __init__(self, api: Facade.API_1) -> None:
        self.__api = api
        self.__computation_data_items: typing.Dict[DataItem.DataItem, str] = dict()
        self.__tool_tip_boxes: typing.List[UserInterface.BoxWidget] = list()

    def __display_item_changed(self, display_item: DisplayItem.DisplayItem) -> None:
        data_item = display_item.data_item if display_item else None
        if data_item:
            tip_id = self.__computation_data_items.get(data_item)
            if tip_id:
                self.__show_tool_tips(tip_id)

    def __show_tool_tips(self, tip_id: str = 'source', timeout: float = 30.0) -> None:
        for box in self.__tool_tip_boxes:
            typing.cast(typing.Any, box).remove_now()
        self.__tool_tip_boxes = list()
        if tip_id == 'source':
            text = 'In the "Computation" panel (Window -> Computation) you find the settings. Custom gain correction mode uses the gain image from the drag-and-drop area.\n"Bin data to 1D" will ouput 1D spectra instead of 2D camera frames in the result data item.\nSelect the "Frame average..." data item for further options.'
        elif tip_id == 'average':
            text = 'Use the three graphics to select the area of the detector that contains the spectra and a part of the detector that was not illuminated on top and below the spectrum (top dark area and bottom dark area).'
        elif tip_id == 'corrected':
            text = 'In the "Computation" panel (Window -> Computation) you find the settings. Custom gain correction mode uses the gain image from the drag-and-drop area.\n"Bin data to 1D" will ouput 1D spectra instead of 2D camera frames in the result data item.\nSelect the "Frame average..." data item for further options.'
        elif tip_id == 'wrong_shape':
            text = 'This computation only works for 4D-data.'
        else:
            return
        document_controller = self.__api.application.document_windows[0]
        #box = document_controller.show_tool_tip_box(text, timeout)
        workspace = document_controller._document_controller.workspace_controller
        assert workspace
        tool_tip_box = workspace.pose_tool_tip_box(text, timeout)
        if tool_tip_box:
            #box = document_controller.show_tool_tip_box(text, timeout)
            self.__tool_tip_boxes.append(tool_tip_box)

    def menu_item_execute(self, window: Facade.DocumentWindow) -> None:
        document_controller = window._document_controller
        data_item = document_controller.selected_data_item
        if data_item:
            try:
                average_data_item, dark_corrected_data_item = framewise_correction_4D(self.__api, window, Facade.DataItem(data_item), 'auto', True, None)
                self.__computation_data_items.update({data_item: 'source',
                                                      average_data_item._data_item: 'average',
                                                      dark_corrected_data_item._data_item: 'corrected'})
                self.__show_tool_tips()
                self.__display_item_changed_event_listener = (document_controller.focused_display_item_changed_event.listen(self.__display_item_changed))
            except Exception as e:
                self.__show_tool_tips(str(e))


def framewise_correction_4D(api: Facade.API_1, window: Facade.DocumentWindow, data_item: Facade.DataItem, gain_mode: str, is_binned: bool, gain_image: Facade.DataItem | None) -> tuple[Facade.DataItem, Facade.DataItem]:
    if not data_item.xdata or not data_item.xdata.is_data_4d:
        raise ValueError("Data item must be 4D.")
    document_controller = window._document_controller
    document_model = document_controller.document_model
    average_data_item = api.library.create_data_item()
    average_data_item.data = np.zeros(data_item.xdata.data_shape[:2], dtype=float)
    api.library.create_computation('nion.calculate_4d_average',
                                   inputs={'src': data_item},
                                   outputs={'target': average_data_item})
    average_display_item = document_model.get_display_item_for_data_item(average_data_item._data_item)
    assert average_display_item
    document_controller.show_display_item(average_display_item)
    spectrum_graphic = average_data_item.add_rectangle_region(0.5, 0.5, 0.1, 1.0)
    spectrum_graphic.label = 'Spectrum'
    bottom_dark_graphic = average_data_item.add_rectangle_region(0.7, 0.5, 0.1, 1.0)
    bottom_dark_graphic.label = 'Bottom dark area'
    top_dark_graphic = average_data_item.add_rectangle_region(0.3, 0.5, 0.1, 1.0)
    top_dark_graphic.label = 'Top dark area'
    spectrum_graphic._graphic.is_bounds_constrained = True
    bottom_dark_graphic._graphic.is_bounds_constrained = True
    top_dark_graphic._graphic.is_bounds_constrained = True

    dark_corrected_data_item = Facade.DataItem(DataItem.DataItem(large_format=True))
    document_model.append_data_item(dark_corrected_data_item._data_item)
    dark_corrected_data_item._data_item.session_id = document_model.session_id
    api.library.create_computation('nion.framewise_dark_correction',
                                   inputs={'src1': data_item,
                                           'src2': average_data_item,
                                           'spectrum_region': spectrum_graphic,
                                           'top_dark_region': top_dark_graphic,
                                           'bottom_dark_region': bottom_dark_graphic,
                                           'bin_spectrum': True,
                                           'gain_image': [],
                                           'gain_mode': 'custom'},
                                   outputs={'target': dark_corrected_data_item})
    dark_corrected_display_item = document_model.get_display_item_for_data_item(dark_corrected_data_item._data_item)
    assert dark_corrected_display_item
    document_controller.show_display_item(dark_corrected_display_item)
    return average_data_item, dark_corrected_data_item


def register_computations() -> None:
    Symbolic.register_computation_type('nion.calculate_4d_average', CalculateAverage4D)
    Symbolic.register_computation_type('nion.framewise_dark_correction', FramewiseDarkCorrection)


class FramewiseDarkExtension:

    # required for Swift to recognize this as an extension class.
    extension_id = "nion.extension.framewise_dark_correction"

    def __init__(self, api_broker: typing.Any) -> None:
        # grab the api object.
        api = api_broker.get_api(version="1", ui_version="1")
        # be sure to keep a reference or it will be closed immediately.
        self.__menu_item_ref = api.create_menu_item(FramewiseDarkMenuItem(api))
        register_computations()

    def close(self) -> None:
        self.__menu_item_ref.close()
