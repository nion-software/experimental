# system imports
import gettext
import logging
import copy
import numpy
import typing

# local libraries
from nion.typeshed import API_1_0 as API
from nion.data import xdata_1_0 as xd
from nion.swift import Facade
from nion.swift.model import Symbolic

_ = gettext.gettext


class SequenceJoin(Symbolic.ComputationHandlerLike):
    computation_id = "nion.join_sequence"
    label = _("Join Sequence")
    inputs = {"src_list": {"label": _("Source data item list")}}
    outputs = {"target": {"label": _("Joined sequence")}}

    def __init__(self, computation: Facade.Computation, **kwargs: typing.Any) -> None:
        self.computation = computation

    def execute(self, *, src_list: typing.Optional[typing.Sequence[Facade.DataItem]] = None, **kwargs: typing.Any) -> None:
        src_list = src_list or list()
        self.__new_xdata = xd.sequence_join([data_item.xdata for data_item in src_list if data_item.xdata])

    def commit(self) -> None:
        self.computation.set_referenced_xdata("target", self.__new_xdata)


class SequenceSplit(Symbolic.ComputationHandlerLike):
    computation_id = "nion.split_sequence"
    label = _("Split Sequence")
    inputs = {"src": {"label": _("Source data item"), "data_type": "xdata"}}
    outputs = {"target": {"label": _("Split sequence")}}

    def __init__(self, computation: Facade.Computation, **kwargs: typing.Any) -> None:
        self.computation = computation

    def execute(self, *, src: typing.Optional[Facade.DataItem] = None, **kwargs: typing.Any) -> None:
        assert src and src.xdata
        self.__new_xdata_list = xd.sequence_split(src.xdata)

    def commit(self) -> None:
        if self.__new_xdata_list:
            for i, xdata in enumerate(self.__new_xdata_list):
                self.computation.set_referenced_xdata(f"target_{i}", xdata)


def sequence_join(api: Facade.API_1, window: Facade.DocumentWindow, display_items: typing.Sequence[Facade.Display]) -> Facade.DataItem | None:
    data_items = list()

    # Check if it makes sense to copy display properties from the source to the result display item.
    # For line plots with multiple display layers we want to copy the display properties so that the joined item
    # look like the original display items. We copy the display properties of the first display item, but only
    # if the number of display layers is the same for all input display items.
    display_layers = None
    legend_position = None
    display_type = None
    copy_display_properties = False
    for i, display_item in enumerate(display_items):
        data_item = display_item.data_items[0] if display_item and len(display_item.data_items) > 0 else None

        if data_item:
            data_items.append(data_item)
            data_item_xdata = data_item.xdata
            if (data_item_xdata is not None and len(display_item.data_items) == 1 and len(data_item_xdata.data_shape) > 1 and
                    (data_item_xdata.datum_dimension_count == 1 or display_item.display_type == 'line_plot')):
                if i== 0:
                    display_layers = copy.deepcopy(display_item._display_item.display_layers)
                    legend_position = display_item._display_item.get_display_property('legend_position')
                    display_type = display_item._display_item.display_type
                    copy_display_properties = True
                elif display_layers is not None:
                    copy_display_properties &= len(display_layers) == len(display_item._display_item.display_layers)

    if not data_items:
        return None

    result_data_item = api.library.create_data_item_from_data(numpy.zeros((1, 1, 1)))
    api.library.create_computation("nion.join_sequence",
                                   inputs={"src_list": data_items},
                                   outputs={"target": result_data_item})
    result_display_item = window._document_controller.document_model.get_display_item_for_data_item(result_data_item._data_item)
    if result_display_item:
        window._document_controller.show_display_item(result_display_item)

        if copy_display_properties:
            if display_layers is not None:
                while result_display_item.display_layers:
                    result_display_item.remove_display_layer(0)
                for display_layer in display_layers:
                    result_display_item.append_display_layer(display_layer)
            if legend_position is not None:
                result_display_item.set_display_property('legend_position', legend_position)
            if display_type is not None:
                result_display_item.display_type = display_type

    return result_data_item


class SequenceJoinMenuItem:
    menu_id = "_processing_menu"  # required, specify menu_id where this item will go
    menu_item_name = _("Join Sequence(s)")  # menu item name

    def __init__(self, api: Facade.API_1) -> None:
        self.__api = api

    def menu_item_execute(self, window: API.DocumentWindow) -> None:
        document_controller = window._document_controller
        selected_display_items = document_controller.selected_display_items
        sequence_join(self.__api, window, [Facade.Display(display_item) for display_item in selected_display_items])


def sequence_split(api: Facade.API_1, window: Facade.DocumentWindow, display_item: Facade.Display) -> typing.Sequence[Facade.DataItem]:
    # Check if it makes sense to copy display properties from the source to the result display item.
    # For line plots with multiple display layers we want to copy the display properties so that the split items
    # look like the original display item. Exclude case where the display layers are generated from the sequence
    # dimension because in this case the display layers are not valid anymore.
    display_layers = None
    legend_position = None
    display_type = None
    data_item_ = display_item._display_item.data_item
    assert data_item_
    data_item = Facade.DataItem(data_item_)
    data_item_xdata = data_item.xdata
    if not data_item_xdata:
        return list()
    if data_item_xdata and len(data_item_xdata.data_shape) > 2 and (data_item_xdata.datum_dimension_count == 1 or display_item.display_type == 'line_plot'):
        display_layers = copy.deepcopy(display_item._display_item.display_layers)
        legend_position = display_item._display_item.get_display_property('legend_position')
        display_type = display_item.display_type

    result_data_items = dict[str, Facade.DataItem]()

    if data_item_xdata.is_sequence:
        split_count = data_item_xdata.data_shape[0]
        if split_count > 100:
            logging.error("Splitting sequences of more than 100 items is disabled for performance reasons.")
            return list()
        result_data_items = {f"target_{i}": api.library.create_data_item(title=f"{data_item.title} (Split {i + 1}/{split_count})") for i in range(split_count)}
        api.library.create_computation("nion.split_sequence",
                                       inputs={"src": data_item},
                                       outputs=result_data_items)

        for result_data_item in result_data_items.values():
            result_display_item = window._document_controller.document_model.get_display_item_for_data_item(result_data_item._data_item)
            if result_display_item:
                window._document_controller.show_display_item(result_display_item)

                if display_layers:
                    while result_display_item.display_layers:
                        result_display_item.remove_display_layer(0)
                    for display_layer in display_layers:
                        result_display_item.append_display_layer(display_layer)
                if legend_position is not None:
                    result_display_item.set_display_property('legend_position', legend_position)
                if display_type is not None:
                    result_display_item.display_type = display_type

    return list(result_data_items.values())


class SequenceSplitMenuItem:
    menu_id = "_processing_menu"
    menu_item_name = _("Split Sequence")

    def __init__(self, api: Facade.API_1) -> None:
        self.__api = api

    def menu_item_execute(self, window: API.DocumentWindow) -> None:
        document_controller = window._document_controller
        display_item = document_controller.selected_display_item
        data_item = display_item.data_items[0] if display_item and len(display_item.data_items) > 0 else None

        if not data_item:
            return

        sequence_split(self.__api, window, Facade.Display(display_item))


class SequenceSplitJoinExtension:

    # required for Swift to recognize this as an extension class.
    extension_id = "nion.extension.sequence_split_join"

    def __init__(self, api_broker: typing.Any) -> None:
        # grab the api object.
        api = api_broker.get_api(version="1", ui_version="1")
        # be sure to keep a reference or it will be closed immediately.
        self.__join_menu_item_ref = api.create_menu_item(SequenceJoinMenuItem(api))
        self.__split_menu_item_ref = api.create_menu_item(SequenceSplitMenuItem(api))

    def close(self) -> None:
        self.__join_menu_item_ref.close()
        self.__split_menu_item_ref.close()


Symbolic.register_computation_type(SequenceJoin.computation_id, SequenceJoin)
Symbolic.register_computation_type(SequenceSplit.computation_id, SequenceSplit)
