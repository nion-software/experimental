import gettext
import numpy
import numpy.typing
import scipy.ndimage
import typing

from nion.data import DataAndMetadata
from nion.swift import Facade
from nion.swift.model import Symbolic

_ = gettext.gettext


def function_find_local_maxima(input_xdata: DataAndMetadata._DataAndMetadataLike, spacing: int = 5, number_maxima: int = 10) -> typing.Tuple[typing.List[typing.Tuple[int, ...]], typing.List[float]]:
    input_xdata = DataAndMetadata.promote_ndarray(input_xdata)

    max_filtered = scipy.ndimage.maximum_filter(input_xdata.data, size=spacing)
    local_maxima = max_filtered == input_xdata.data

    if numpy.ndim(input_xdata.data) == 1:
        footprint = numpy.array([True, False, True], dtype=bool)
    elif numpy.ndim(input_xdata.data) == 2:
        footprint = numpy.array([[True, True, True],
                                 [True, False, True],
                                 [True, True, True]], dtype=bool)
    else:
        raise ValueError(f'Only one- and two-dimensional data is supported by this function but input has {numpy.ndim(input_xdata.data)} dimensions.')

    # We want to select only the points that actually stand out from their neighbors. So far, also a flat region would
    # be marked as local maximum.
    selection_filtered = scipy.ndimage.maximum_filter(input_xdata.data, footprint=footprint)
    selection = selection_filtered == input_xdata.data
    local_maxima = numpy.logical_and(local_maxima, numpy.logical_not(selection))

    if numpy.ndim(input_xdata.data) == 1:
        x = numpy.mgrid[:local_maxima.shape[0]]
        max_x, max_val = x[local_maxima], input_xdata.data[local_maxima]
        max_list = sorted(zip(max_x, max_val), key=lambda key: key[-1], reverse=True) # type: ignore # mypy doesn't seem to recognize this
    elif numpy.ndim(input_xdata.data) == 2:
        y, x = numpy.mgrid[:local_maxima.shape[0], :local_maxima.shape[1]]
        max_y, max_x, max_val = y[local_maxima], x[local_maxima], input_xdata.data[local_maxima]
        max_list = sorted(zip(max_y, max_x, max_val), key=lambda key: key[-1], reverse=True) # type: ignore # mypy doesn't seem to recognize this
    else:
        raise ValueError(f'Only one- and two-dimensional data is supported by this function but input has {numpy.ndim(input_xdata.data)} dimensions.')

    if len(max_list) > number_maxima:
        max_list = max_list[:number_maxima]

    return [item[:-1] for item in max_list], [item[-1] for item in max_list]


class FindLocalMaxima(Symbolic.ComputationHandlerLike):
    computation_id = "nion.find_local_maxima"
    label = _("Find Local Maxima")
    inputs = {"input_data_item": {"label": _("Input data item"), "data_type": "xdata"},
              "spacing": {"label": _("Spacing")},
              "number_maxima": {"label": _("Number maxima")}}
    outputs = dict[str, typing.Any]()

    def __init__(self, computation: Facade.Computation, **kwargs: typing.Any) -> None:
        self.computation = computation
        self.__max_points: typing.List[typing.Tuple[int, ...]] = []
        self.__max_vals: typing.List[float] = []
        self.__src: typing.Optional[Facade.DataItem] = None
        self.__api = Facade.get_api(version="~1.0")

    def execute(self, *, input_data_item: Facade.DataItem, spacing: int, number_maxima: int, **kwargs: typing.Any) -> None: # type: ignore
        assert input_data_item.display_xdata is not None

        self.__max_points, self.__max_vals = function_find_local_maxima(input_data_item.display_xdata.data, spacing, number_maxima)
        self.__src = input_data_item

        return None

    def commit(self) -> None:
        if self.__max_points and self.__src:
            assert self.__src.display_xdata is not None
            max_grapics = self.computation.get_result("max_graphics", None)
            with self.__api.library.data_ref_for_data_item(self.__src):
                if max_grapics:
                    for graphic in max_grapics:
                        self.__src.remove_region(graphic)
                shape = self.__src.display_xdata.data.shape
                max_graphics = []
                if numpy.ndim(self.__src.display_xdata.data) == 1:
                    for point, value in zip(self.__max_points, self.__max_vals):
                        graphic = self.__src.add_channel_region((point[0] + 0.5) / shape[0])
                        graphic.label = f'{value:.3g}'
                        max_graphics.append(graphic)
                else:
                    for point, value in zip(self.__max_points, self.__max_vals):
                        graphic = self.__src.add_point_region((point[0] + 0.5) / shape[0], (point[1] + 0.5) / shape[1])
                        graphic.label = f'{value:.3g}'
                        max_graphics.append(graphic)

            self.computation.set_result("max_graphics", max_graphics)


class FindLocalMaximaMenuItemDelegate:
    def __init__(self, api: Facade.API_1) -> None:
        self.__api  = api
        self.menu_id = "_processing_menu"

    @property
    def menu_item_name(self) -> str:
        return _("[EXPERIMENTAL] Find local maxima")

    def menu_item_execute(self, window: Facade.DocumentWindow) -> None:
        selected_data_item = window.target_data_item

        if not selected_data_item or not selected_data_item.xdata:
            return

        self.__api.library.create_computation("nion.find_local_maxima",
                                              inputs={"input_data_item": selected_data_item,
                                                      "spacing": 5,
                                                      "number_maxima": 10},
                                              outputs={"max_graphics": None})
        return None


class FindLocalMaximaExtension:

    extension_id = "nion.experimental.find_local_maxima"

    def __init__(self, api_broker: typing.Any) -> None:
        api = typing.cast(Facade.API_1, api_broker.get_api(version="~1.0"))
        self.__find_local_maxima_menu_item_ref = api.create_menu_item(FindLocalMaximaMenuItemDelegate(api))

    def close(self) -> None:
        self.__find_local_maxima_menu_item_ref.close()
        self.__find_local_maxima_menu_item_ref = typing.cast(Facade.API_1.MenuItemReference, None)
        return None


Symbolic.register_computation_type(FindLocalMaxima.computation_id, FindLocalMaxima)
