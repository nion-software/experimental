import typing
import gettext
import numpy

from nion.swift.model import DocumentModel
from nion.swift import Facade as API

_ = gettext.gettext

processing_descriptions = {"nion.processing.i_e_square_plot":
                               {"title": _("I E^2 Plot"), "expression": "src.xdata * xd.axis_coordinates(src.xdata, -1)**2","sources": [{"name": "src", "label": _("Source")}]}
                           }


DocumentModel.DocumentModel.register_processing_descriptions(processing_descriptions)


class IESquarePlotMenuItemDelegate:
    def __init__(self, api: API.API_1):
        self.__api = api
        self.menu_id = "eels_menu"
        self.menu_name = _("EELS")
        self.menu_before_id = "window_menu"
        self.menu_item_name = _("[EXPERIMENTAL] I E^2 Plot")

    def menu_item_execute(self, window: API.DocumentWindow):
        selected_display_item = window._document_window.selected_display_item
        if not selected_display_item or not selected_display_item.data_item or not selected_display_item.data_item.xdata:
            return
        window._document_window.document_model.get_processing_new("nion.processing.i_e_square_plot", selected_display_item, selected_display_item.data_item)


class IESquarePlotExtension:

    extension_id = "nion.experimental.i_e_square_plot"

    def __init__(self, api_broker: typing.Any):
        api = typing.cast(API.API_1, api_broker.get_api(version="~1.0"))
        self.__i_e_square_plot_menu_item_ref = api.create_menu_item(IESquarePlotMenuItemDelegate(api))

    def close(self):
        self.__i_e_square_plot_menu_item_ref.close()
        self.__i_e_square_plot_menu_item_ref = None
