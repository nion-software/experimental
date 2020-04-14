# system imports
import gettext

import numpy as np

# local libraries
from nion.typeshed import API_1_0 as API
from nion.data import xdata_1_0 as xd
from nion.swift import Facade
from nion.swift.model import Symbolic
from nion.utils import Binding

from .DataCache import DataCache

_ = gettext.gettext


class Map4D:
    def __init__(self, computation, **kwargs):
        self.computation = computation
        self.__api = computation.api
        if not hasattr(computation, 'data_cache'):
            def modify_data_fn(data):
                new_shape = data.shape[:2] + (-1,)
                return np.reshape(data, new_shape)
            computation.data_cache = DataCache(modify_data_fn=modify_data_fn)

        def create_panel_widget(ui, document_controller):
            def select_button_clicked():
                graphics = document_controller.target_display.selected_graphics
                if not graphics:
                    return
                graphics_variable = self.computation._computation._get_variable('map_regions')
                num_items = len(graphics_variable.objects_model.items)
                for _ in range(num_items):
                    graphics_variable.objects_model.remove_item(0)
                for graphic in graphics:
                    graphics_variable.objects_model.append_item(graphic.specifier.rpc_dict)

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

        self.computation._computation.create_panel_widget = create_panel_widget

    def execute(self, src, map_regions):
        try:
            data = self.computation.data_cache.get_cached_data(src)
            mask_data = np.zeros(src.xdata.data_shape[2:], dtype=np.bool)
            for region in map_regions:
                mask_data = np.logical_or(mask_data, region.get_mask(src.xdata.data_shape[2:]))
            if mask_data.any():
                ind = np.arange(mask_data.size)[mask_data.ravel()]
                new_data = np.sum(data[..., ind], axis=(-1))
                # y = np.unique(np.indices(mask_data.shape)[0][mask_data])
                # x = np.unique(np.indices(mask_data.shape)[1][mask_data])
                # new_data = np.sum(xdata.data[..., x][..., y, :], axis=(-2, -1))
            else:
                new_data = np.sum(data, axis=-1)
            self.__new_xdata = self.__api.create_data_and_metadata(new_data,
                                                                   dimensional_calibrations=src.dimensional_calibrations[:2],
                                                                   intensity_calibration=src.intensity_calibration)
            metadata = src.metadata.copy()
            metadata['nion.map_4d.parameters'] = {'src': src._data_item.write_to_dict(),
                                                  'map_regions': [region.write_to_dict() for region in map_regions]}
            self.__new_xdata.metadata.update(metadata)
        except Exception as e:
            print(str(e))
            import traceback
            traceback.print_exc()

    def commit(self):
        self.computation.set_referenced_xdata('target', self.__new_xdata)


class Map4DMenuItem:

    menu_id = "4d_tools_menu"  # required, specify menu_id where this item will go
    menu_name = _("4D Tools") # optional, specify default name if not a standard menu
    menu_before_id = "window_menu" # optional, specify before menu_id if not a standard menu
    menu_item_name = _("Map 4D")  # menu item name

    def __init__(self, api):
        self.__api = api
        self.__computation_data_items = dict()
        self.__tool_tip_boxes = list()

    def __display_item_changed(self, display_item):
        data_item = display_item.data_item if display_item else None
        if data_item:
            tip_id = self.__computation_data_items.get(str(data_item.uuid))
            if tip_id:
                self.__show_tool_tips(tip_id)

    def __show_tool_tips(self, tip_id='source', timeout=30):
        for box in self.__tool_tip_boxes:
            box.remove_now()
        self.__tool_tip_boxes = list()
        if tip_id == 'source':
            text = ('Select one or multiple graphic(s) on the source data item and click "Select" in the computation '
                    'panel (Window -> Computation).\nWithout a selected graphic, the whole frames will be summed.')
        elif tip_id == 'map_4d':
            text = 'Move the "Pick" graphic to change the data slice in the source data item.'
        elif tip_id == 'wrong_shape':
            text = 'This computation only works for 4D-data.'
        else:
            return
        document_controller = self.__api.application.document_windows[0]
        workspace = document_controller._document_controller.workspace_controller
        box = workspace.pose_tool_tip_box(text, timeout)
        #box = document_controller.show_tool_tip_box(text, timeout)
        self.__tool_tip_boxes.append(box)

    def menu_item_execute(self, window: API.DocumentWindow) -> None:
        document_controller = window._document_controller
        selected_display_item = document_controller.selected_display_item
        data_item = (selected_display_item.data_items[0] if
                     selected_display_item and len(selected_display_item.data_items) > 0 else None)

        if data_item:
            api_data_item = Facade.DataItem(data_item)
            if not api_data_item.xdata.is_data_4d:
                self.__show_tool_tips('wrong_shape')
                return
            map_data_item = self.__api.library.create_data_item(title='Map 4D of ' + data_item.title)
            computation = self.__api.library.create_computation('nion.map_4d',
                                                                inputs={'src': api_data_item,
                                                                        'map_regions': []},
                                                                outputs={'target': map_data_item})
            computation._computation.source = map_data_item._data_item

            map_display_item = document_controller.document_model.get_display_item_for_data_item(map_data_item._data_item)
            document_controller.show_display_item(map_display_item)
            pick_graphic = map_data_item.add_point_region(0.5, 0.5)
            pick_graphic.label = 'Pick'
            def update_collection_index_0(value):
                collection_index = selected_display_item.display_data_channel.collection_index
                if value != collection_index[0]:
                    selected_display_item.display_data_channel.collection_index = (value, collection_index[1], 0)

            def update_collection_index_1(value):
                collection_index = selected_display_item.display_data_channel.collection_index
                if value != collection_index[1]:
                    selected_display_item.display_data_channel.collection_index = (collection_index[0], value, 0)
            computation.pick_graphic_binding_0 = Binding.TuplePropertyBinding(pick_graphic._graphic, 'position', 0, converter=FloatTupleToIntTupleConverter(data_item.data.shape[0], 0))
            computation.pick_graphic_binding_1 = Binding.TuplePropertyBinding(pick_graphic._graphic, 'position', 1, converter=FloatTupleToIntTupleConverter(data_item.data.shape[1], 1))
            computation.pick_graphic_binding_0.target_setter = update_collection_index_0
            computation.pick_graphic_binding_1.target_setter = update_collection_index_1
            def collection_index_changed(key):
                if key == 'collection_index':
                    collection_index = selected_display_item.display_data_channel.collection_index
                    if int(pick_graphic.position[0]*data_item.data.shape[0]) != collection_index[0]:
                        computation.pick_graphic_binding_0.update_source(collection_index)
                    if int(pick_graphic.position[1]*data_item.data.shape[1]) != collection_index[1]:
                        computation.pick_graphic_binding_1.update_source(collection_index)
            computation.collection_index_changed_event_listener = selected_display_item.display_data_channel.property_changed_event.listen(collection_index_changed)
            self.__computation_data_items.update({str(data_item.uuid): 'source',
                                                  str(map_data_item._data_item.uuid): 'map_4d'})
            self.__show_tool_tips()
            self.__display_item_changed_event_listener = (
                           document_controller.focused_display_item_changed_event.listen(self.__display_item_changed))

class FloatTupleToIntTupleConverter:
    def __init__(self, axis_size, axis_index):
        self.axis_size = axis_size
        self.axis_index = axis_index

    def convert(self, value):
        return int(value*self.axis_size)

    def convert_back(self, value):
        return (value[self.axis_index] + 0.5)/self.axis_size


class Map4DExtension:

    # required for Swift to recognize this as an extension class.
    extension_id = "nion.extension.map_4d"

    def __init__(self, api_broker):
        # grab the api object.
        api = api_broker.get_api(version="1", ui_version="1")
        # be sure to keep a reference or it will be closed immediately.
        self.__menu_item_ref = api.create_menu_item(Map4DMenuItem(api))

    def close(self):
        self.__menu_item_ref.close()

Symbolic.register_computation_type('nion.map_4d', Map4D)
