# system imports
import copy
import gettext
import typing

import numpy as np

from nion.data import Calibration
from nion.data import DataAndMetadata
from nion.data import xdata_1_0 as xd
from nion.swift import Facade
from nion.swift.ComputationPanel import make_image_chooser
from nion.swift.model import DocumentModel
from nion.swift.model import DisplayItem
from nion.swift.model import Graphics
from nion.swift.model import Symbolic
from nion.ui import UserInterface

_ = gettext.gettext

_DataArrayType = np.typing.NDArray[typing.Any]


class CenterOfMass4D:
    label = _("Center of Mass Map")
    attributes = {"connection_type": "map"}

    def __init__(self, computation: Facade.Computation, **kwargs: typing.Any) -> None:
        self.computation = computation

        def create_panel_widget(ui: Facade.UserInterface, document_controller: Facade.DocumentWindow) -> Facade.ColumnWidget:
            def select_button_clicked() -> None:
                graphics = document_controller.target_display.selected_graphics if document_controller.target_display else None
                if not graphics:
                    return
                try:
                    while True:
                        self.computation._computation.remove_item_from_objects('map_regions', 0)
                except IndexError:
                    pass
                for graphic in graphics:
                    self.computation._computation.insert_item_into_objects('map_regions', 0, Symbolic.make_item(graphic._graphic))

            column = ui.create_column_widget()
            row = ui.create_row_widget()

            select_graphics_button = ui.create_push_button_widget('Select map graphic')
            row.add_spacing(10)
            row.add(select_graphics_button)
            row.add_stretch()
            row.add_spacing(10)

            column.add_spacing(10)
            column.add(row)
            column.add_spacing(10)
            column.add_stretch()

            select_graphics_button.on_clicked = select_button_clicked

            return column

        typing.cast(typing.Any, self.computation._computation).create_panel_widget = create_panel_widget

    def execute(self, src: Facade.DataSource | None = None, map_regions: typing.Sequence[Graphics.Graphic] | None = None, **kwargs: typing.Any) -> None:
        assert src is not None
        assert map_regions is not None
        src_xdata = src.xdata
        assert src_xdata is not None
        src_data = np.reshape(src_xdata.data, src_xdata.data_shape[:2] + (-1,))  # flatten the last two dimensions
        mask_data = np.zeros(src_xdata.data_shape[2:], dtype=np.bool_)
        for region in map_regions:
            mask_data = np.logical_or(mask_data, region.get_mask(src_xdata.data_shape[2:]))
        grid_y, grid_x = np.mgrid[:mask_data.shape[0], :mask_data.shape[1]]
        if mask_data.any():
            ind = np.arange(mask_data.size)[mask_data.ravel()]
            selected_data = src_data[..., ind]
            selected_data_sum = np.sum(selected_data, axis=-1)
            com_y = (grid_y[mask_data]).ravel()
            com_x = (grid_x[mask_data]).ravel()
            new_data = np.array((np.sum(com_y * selected_data, axis=-1) / selected_data_sum,
                                 np.sum(com_x * selected_data, axis=-1) / selected_data_sum), dtype=np.float32)
            # y = np.unique(np.indices(mask_data.shape)[0][mask_data])
            # x = np.unique(np.indices(mask_data.shape)[1][mask_data])
            # new_data = np.sum(xdata.src_data[..., x][..., y, :], axis=(-2, -1))
        else:
            data_sum = np.sum(src_data, axis=-1)
            new_data = np.array((np.sum(src_data * grid_y.ravel(), axis=-1) / data_sum,
                                 np.sum(src_data * grid_x.ravel(), axis=-1)) / data_sum, dtype=np.float32)
        data_descriptor = DataAndMetadata.DataDescriptor(True, 0, 2)
        empty_calibration = Calibration.Calibration()
        intensity_calibration = Calibration.Calibration(units='px')
        dimensional_calibrations = [empty_calibration] + list(src_xdata.dimensional_calibrations)[:2]
        self.__new_xdata = DataAndMetadata.new_data_and_metadata(new_data,
                                                                 dimensional_calibrations=dimensional_calibrations,
                                                                 intensity_calibration=intensity_calibration,
                                                                 data_descriptor=data_descriptor)

    def commit(self) -> None:
        self.computation.set_referenced_xdata('target', self.__new_xdata)


class CenterOfMass4DMenuItem:

    menu_id = "4d_tools_menu"  # required, specify menu_id where this item will go
    menu_name = _("4D Tools") # optional, specify default name if not a standard menu
    menu_before_id = "window_menu" # optional, specify before menu_id if not a standard menu
    menu_item_name = _("Center of Mass 4D")  # menu item name

    def __init__(self, api: Facade.API_1) -> None:
        self.__api = api
        self.__computation_data_items: typing.Dict[str, str] = dict()
        self.__tool_tip_boxes: typing.List[UserInterface.BoxWidget] = list()

    def __display_item_changed(self, display_item: DisplayItem.DisplayItem) -> None:
        data_item = display_item.data_item if display_item else None
        if data_item:
            tip_id = self.__computation_data_items.get(str(data_item.uuid))
            if tip_id:
                self.__show_tool_tips(tip_id)

    def __show_tool_tips(self, tip_id: str = 'source', timeout: float = 30.0) -> None:
        for box in self.__tool_tip_boxes:
            typing.cast(typing.Any, box).remove_now()
        self.__tool_tip_boxes = list()
        if tip_id == 'source':
            text = ('Select one or multiple graphic(s) on the source data item and click "Select" in the computation '
                    'panel (Window -> Computation).\nWithout a selected graphic, the whole center-of-mass will be '
                    'calculated for the full frames.')
        elif tip_id == 'center_of_mass_4d':
            text = ('Move the "Pick" graphic to change the data slice in the source data item.\n'
                    'X- and y-coordinates of the COM are in the two slices of the result (order: y, x).')
        elif tip_id == 'wrong_shape':
            text = 'This computation only works for 4D-data.'
        else:
            return
        document_controller = self.__api.application.document_windows[0]
        workspace = document_controller._document_controller.workspace_controller
        assert workspace
        tool_tip_box = workspace.pose_tool_tip_box(text, timeout)
        if tool_tip_box:
            #box = document_controller.show_tool_tip_box(text, timeout)
            self.__tool_tip_boxes.append(tool_tip_box)

    def menu_item_execute(self, window: Facade.DocumentWindow) -> None:
        document_controller = window._document_controller
        display_item = document_controller.selected_display_item
        data_item = display_item.data_item if display_item else None
        if display_item and data_item:
            try:
                map_data_item = center_of_mass_4D(self.__api, window, Facade.Display(display_item), [])
                self.__computation_data_items.update({str(data_item.uuid): 'source', str(map_data_item._data_item.uuid): 'center_of_mass_4d'})
                self.__show_tool_tips()
                self.__display_item_changed_event_listener = document_controller.focused_display_item_changed_event.listen(self.__display_item_changed)
            except Exception as e:
                self.__show_tool_tips(str(e))


def center_of_mass_4D(api: Facade.API_1, window: Facade.DocumentWindow, display_item: Facade.Display, map_regions: typing.Sequence[Facade.Graphic]) -> Facade.DataItem:
    display_data_channel = display_item._display_item.display_data_channel
    if not display_data_channel:
        raise ValueError("Display item must have a single display.")
    assert display_data_channel.data_item
    data_item = Facade.DataItem(display_data_channel.data_item)
    if not data_item.xdata or not data_item.xdata.is_data_4d:
        raise ValueError("Data item must be 4D.")
    document_model = api.library._document_model
    map_data_item = api.library.create_data_item()
    # the following uses internal API and should not be used as example code.
    computation = document_model.create_computation()
    computation.create_input_item("src", Symbolic.make_item(display_data_channel))
    computation.create_input_item("map_regions", Symbolic.make_item_list([map_region._graphic for map_region in map_regions]))
    computation.processing_id = "nion.center_of_mass_4d.2"
    document_model.set_data_item_computation(map_data_item._data_item, computation)
    map_display_item = document_model.get_display_item_for_data_item(map_data_item._data_item)
    assert map_display_item
    window._document_controller.show_display_item(map_display_item)
    graphic = Graphics.PointGraphic()
    graphic.label = "Pick"
    graphic.role = "collection_index"
    map_display_item.add_graphic(graphic)
    return map_data_item


class Map4DExtension:

    # required for Swift to recognize this as an extension class.
    extension_id = "nion.extension.center_of_mass_4d"

    def __init__(self, api_broker: typing.Any) -> None:
        # grab the api object.
        api = api_broker.get_api(version="1", ui_version="1")
        # be sure to keep a reference or it will be closed immediately.
        self.__menu_item_ref = api.create_menu_item(CenterOfMass4DMenuItem(api))

    def close(self) -> None:
        self.__menu_item_ref.close()


Symbolic.register_computation_type('nion.center_of_mass_4d.2', CenterOfMass4D)

DocumentModel.DocumentModel.register_processing_descriptions({
    "nion.center_of_mass_4d.2": {
        "title": _("Center of Mass Map"),
        "sources": [
            {"name": "src", "label": _("Source data item"), "data_type": "xdata"},
            {"name": "map_regions", "label": _("Map graphics")}
        ]
    }
})
