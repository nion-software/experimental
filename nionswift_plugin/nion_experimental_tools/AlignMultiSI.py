import typing
import gettext
import numpy

from nion.data import Core
from nion.data import DataAndMetadata
from nion.data import MultiDimensionalProcessing
from nion.swift.model import Symbolic
from nion.swift import Facade
from nion.typeshed import API_1_0 as API
from nion.utils import Event


_ = gettext.gettext


class AlignMultiSI(Symbolic.ComputationHandlerLike):
    label = _("Align and Integrate SI Sequence")
    outputs = {"shifts": {"label": _("Measured Shifts")},
               "aligned_haadf": {"label": _("Aligned HAADF")},
               "aligned_si": {"label": _("Aligned SI")},
               }

    def __init__(self, computation: Facade.Computation, **kwargs: typing.Any) -> None:
        self.computation = computation
        self.progress_updated_event = Event.Event()

        def create_panel_widget(ui: Facade.UserInterface, document_controller: Facade.DocumentWindow) -> Facade.ColumnWidget:
            def update_align_region_label() -> None:
                current_region = self.computation._computation.get_input("align_region")
                haadf_sequence_data_item = self.computation._computation.get_input("haadf_sequence_data_item")
                if current_region and haadf_sequence_data_item:
                    bounds = current_region.bounds
                    shape = haadf_sequence_data_item.xdata.datum_dimension_shape
                    current_region = ((int(bounds[0][0]*shape[0]), int(bounds[0][1]*shape[1])),
                                      (int(bounds[1][0]*shape[0]), int(bounds[1][1]*shape[1])))
                self.align_region_label.text = str(current_region)

            def select_button_clicked() -> None:
                for variable in self.computation._computation.variables:
                    if variable.name == "align_region":
                        self.computation._computation.remove_variable(variable)
                assert document_controller.target_display
                graphics = document_controller.target_display.selected_graphics or list()
                align_region = None
                for graphic in graphics:
                    if graphic.graphic_type == "rect-graphic":
                        align_region = graphic
                        break
                if align_region:
                    self.computation._computation.create_input_item("align_region", Symbolic.make_item(align_region._graphic))

            def align_index_finished(text: str) -> None:
                try:
                    index = int(text)
                except ValueError:
                    current_index = self.computation._computation.get_input_value("align_index") or 0
                    self.index_field.text = str(current_index)
                else:
                    self.computation.set_input_value("align_index", index)

            column = ui.create_column_widget()
            row = ui.create_row_widget()

            select_graphics_button = ui.create_push_button_widget("Select align region")
            self.align_region_label = ui.create_label_widget()
            update_align_region_label()
            row.add_spacing(10)
            row.add(select_graphics_button)
            row.add_spacing(5)
            row.add(ui.create_label_widget("Current region: "))
            row.add(self.align_region_label)
            row.add_stretch()
            row.add_spacing(10)

            index_label = ui.create_label_widget("Reference slice index: ")
            current_index = self.computation._computation.get_input_value("align_index") or 0
            self.index_field = ui.create_line_edit_widget(str(current_index))
            self.index_field.on_editing_finished = align_index_finished
            index_row = ui.create_row_widget()
            index_row.add_spacing(10)
            index_row.add(index_label)
            index_row.add(self.index_field)
            index_row.add_spacing(10)
            index_row.add_stretch()

            column.add_spacing(10)
            column.add(row)
            column.add_spacing(5)
            column.add(index_row)

            select_graphics_button.on_clicked = select_button_clicked

            return column

        typing.cast(typing.Any, self.computation._computation).create_panel_widget = create_panel_widget
        typing.cast(typing.Any, self.computation._computation).progress_updated_event = self.progress_updated_event

    def execute(self, si_sequence_data_item: typing.Optional[API.DataItem] = None,
                haadf_sequence_data_item: typing.Optional[API.DataItem] = None, align_index: int = 0,
                align_region: typing.Optional[API.Graphic] = None, **kwargs: typing.Any) -> None:
        assert haadf_sequence_data_item
        assert si_sequence_data_item
        haadf_xdata = haadf_sequence_data_item.xdata
        si_xdata = si_sequence_data_item.xdata
        bounds = None
        if align_region:
            bounds = align_region.bounds
        translations = Core.function_sequence_measure_relative_translation(haadf_xdata,
                                                                           haadf_xdata[align_index],
                                                                           True, bounds=bounds)
        sequence_shape = haadf_sequence_data_item.xdata.sequence_dimension_shape
        data_zeros = (0,) * si_xdata.datum_dimension_count
        c = int(numpy.prod(sequence_shape))
        haadf_result_data = numpy.empty_like(haadf_xdata.data)
        si_result_data = numpy.empty_like(si_xdata.data)
        for i in range(c):
            ii = numpy.unravel_index(i, sequence_shape)
            current_xdata = DataAndMetadata.new_data_and_metadata(haadf_xdata.data[ii])
            translation = translations.data[ii]
            haadf_result_data[ii] = Core.function_shift(current_xdata, tuple(translation)).data
            current_xdata = DataAndMetadata.new_data_and_metadata(si_xdata.data[ii])
            si_result_data[ii] = Core.function_shift(current_xdata, tuple(translation) + data_zeros).data
            self.progress_updated_event.fire(0, c, i+1)

        self.__aligned_haadf_sequence = DataAndMetadata.new_data_and_metadata(haadf_result_data,
                                                                              intensity_calibration=haadf_xdata.intensity_calibration,
                                                                              dimensional_calibrations=haadf_xdata.dimensional_calibrations,
                                                                              metadata=haadf_xdata.metadata,
                                                                              data_descriptor=haadf_xdata.data_descriptor)
        self.__aligned_si_sequence = DataAndMetadata.new_data_and_metadata(si_result_data,
                                                                           intensity_calibration=si_xdata.intensity_calibration,
                                                                           dimensional_calibrations=si_xdata.dimensional_calibrations,
                                                                           metadata=si_xdata.metadata,
                                                                           data_descriptor=si_xdata.data_descriptor)

    def commit(self) -> None:
        self.computation.set_referenced_xdata("aligned_haadf", self.__aligned_haadf_sequence)
        self.computation.set_referenced_xdata("aligned_si", self.__aligned_si_sequence)


class AlignMultiSI2(Symbolic.ComputationHandlerLike):
    computation_id = "eels.align_multi_si2"
    label = _("Align and Integrate SI Sequence")
    inputs = {"haadf_data_item": {"label": _("HHADF data item"), "data_type": "xdata"},
              "si_data_item": {"label": _("SI data item"), "data_type": "xdata"},
              "reference_index": {"label": _("Reference index for shifts")},
              "relative_shifts": {"label": _("Measure shifts relative to previous slice")},
              "max_shift": {"label": _("Max shift between consecutive frames (in pixels, <= 0 to disable)")},
              "bounds_graphic": {"label": _("Shift bounds")},
              }
    outputs = {"shifts": {"label": _("Measured Shifts")},
               "integrated_haadf": {"label": _("Integrated HAADF")},
               "integrated_si": {"label": _("Integrated SI")},
               }

    def __init__(self, computation: Facade.Computation, **kwargs: typing.Any) -> None:
        self.computation = computation

    def execute(self, *,
                haadf_data_item: typing.Optional[API.DataItem] = None,
                si_data_item: typing.Optional[API.DataItem] = None,
                reference_index: typing.Optional[int] = None, relative_shifts: bool = True,
                max_shift: int = 0, bounds_graphic: typing.Optional[API.Graphic] = None, **kwargs: typing.Any) -> None:
        assert si_data_item
        assert haadf_data_item
        si_xdata = si_data_item.xdata
        haadf_xdata = haadf_data_item.xdata
        bounds = None
        if bounds_graphic is not None:
            bounds = bounds_graphic.bounds
        max_shift_ = max_shift if max_shift > 0 else None
        reference_index = reference_index if not relative_shifts else None
        shifts_axes = tuple(haadf_xdata.datum_dimension_indexes)
        shifts_xdata = MultiDimensionalProcessing.function_measure_multi_dimensional_shifts(haadf_xdata, shifts_axes, reference_index=reference_index, bounds=bounds, max_shift=max_shift_)
        self.__shifts_xdata = Core.function_transpose_flip(shifts_xdata, transpose=True, flip_v=False, flip_h=False)
        aligned_haadf_xdata = MultiDimensionalProcessing.function_apply_multi_dimensional_shifts(haadf_xdata, shifts_xdata.data, shifts_axes)
        assert aligned_haadf_xdata
        self.__integrated_haadf_xdata = Core.function_sum(aligned_haadf_xdata, axis=0)
        shifts_axes = tuple(si_xdata.collection_dimension_indexes)
        aligned_si_xdata = MultiDimensionalProcessing.function_apply_multi_dimensional_shifts(si_xdata, shifts_xdata.data, shifts_axes)
        assert aligned_si_xdata
        self.__integrated_si_xdata = Core.function_sum(aligned_si_xdata, axis=0)

    def commit(self) -> None:
        self.computation.set_referenced_xdata("shifts", self.__shifts_xdata)
        self.computation.set_referenced_xdata("integrated_haadf", self.__integrated_haadf_xdata)
        self.computation.set_referenced_xdata("integrated_si", self.__integrated_si_xdata)


def menu_item_align_multi_si(api: API, window: API.DocumentWindow) -> None:
    selected_display_items = window._document_controller._get_two_data_sources()
    error_msg = "Select a sequence of spectrum images and a sequence of scanned images in order to use this computation."
    assert selected_display_items[0][0] is not None, error_msg
    assert selected_display_items[1][0] is not None, error_msg
    assert selected_display_items[0][0].data_item is not None, error_msg
    assert selected_display_items[1][0].data_item is not None, error_msg
    assert selected_display_items[0][0].data_item.is_sequence, error_msg
    assert selected_display_items[1][0].data_item.is_sequence, error_msg

    if selected_display_items[0][0].data_item.is_collection:
        si_sequence_data_item = Facade.DataItem(selected_display_items[0][0].data_item)
        haadf_sequence_data_item = Facade.DataItem(selected_display_items[1][0].data_item)
        align_region = Facade.Graphic(selected_display_items[1][1]) if selected_display_items[1][1] else None
        align_index = selected_display_items[1][0].display_data_channel.sequence_index
    elif selected_display_items[1][0].data_item.is_collection:
        si_sequence_data_item = Facade.DataItem(selected_display_items[1][0].data_item)
        haadf_sequence_data_item = Facade.DataItem(selected_display_items[0][0].data_item)
        align_region = Facade.Graphic(selected_display_items[0][1]) if selected_display_items[0][1] else None
        align_index = selected_display_items[0][0].display_data_channel.sequence_index
    else:
        raise ValueError(error_msg)

    align_multi_si(api, window, haadf_sequence_data_item, align_region, si_sequence_data_item, align_index)

def align_multi_si(api: API, window: API.DocumentWindow, haadf_sequence_data_item: Facade.DataItem, bounds_graphic: Facade.Graphic | None, si_sequence_data_item: Facade.DataItem, align_index: int) -> tuple[Facade.DataItem, Facade.DataItem]:
    aligned_haadf = api.library.create_data_item()
    aligned_si = api.library.create_data_item()

    inputs = {"si_sequence_data_item": si_sequence_data_item,
              "haadf_sequence_data_item": haadf_sequence_data_item,
              "align_index": align_index}

    if bounds_graphic:
        inputs["align_region"] = bounds_graphic

    api.library.create_computation("eels.align_multi_si",
                                   inputs=inputs,
                                   outputs={"aligned_haadf": aligned_haadf,
                                            "aligned_si": aligned_si})
    window.display_data_item(aligned_haadf)
    window.display_data_item(aligned_si)

    return aligned_haadf, aligned_si


def align_multi_si2(api: API, window: API.DocumentWindow, haadf_sequence_data_item: Facade.DataItem, bounds_graphic: Facade.Graphic | None, si_sequence_data_item: Facade.DataItem) -> tuple[Facade.DataItem, Facade.DataItem, Facade.DataItem]:
    aligned_haadf = api.library.create_data_item()
    aligned_si = api.library.create_data_item()
    shifts = api.library.create_data_item_from_data(numpy.zeros((2, 2)))

    haadf_xdata = haadf_sequence_data_item.xdata
    assert haadf_xdata

    inputs = {"haadf_data_item": {"object": haadf_sequence_data_item, "type": "data_source"},
              "si_data_item": {"object": si_sequence_data_item, "type": "data_source"},
              "reference_index": haadf_xdata.sequence_dimension_shape[0] // 2,
              "relative_shifts": False,
              "max_shift": 3,
              }
    if bounds_graphic:
        inputs["bounds_graphic"] = bounds_graphic

    api.library.create_computation("eels.align_multi_si2",
                                   inputs=inputs,
                                   outputs={"shifts": shifts,
                                            "integrated_haadf": aligned_haadf,
                                            "integrated_si": aligned_si})
    window.display_data_item(aligned_haadf)
    window.display_data_item(aligned_si)
    window.display_data_item(shifts)

    display_item = api.library._document_model.get_display_item_for_data_item(shifts._data_item)
    display_item.display_type = "line_plot"
    display_item._set_display_layer_properties(0, stroke_color='#1E90FF', stroke_width=2, fill_color=None, label="y")
    display_item._set_display_layer_properties(1, stroke_color='#F00', stroke_width=2, fill_color=None, label="x")

    return aligned_haadf, aligned_si, shifts


Symbolic.register_computation_type("eels.align_multi_si", AlignMultiSI)
Symbolic.register_computation_type(AlignMultiSI2.computation_id, AlignMultiSI2)


class AlignMultiSIMenuItemDelegate:

    def __init__(self, api: Facade.API_1) -> None:
        self.__api = api
        self.menu_id = "eels_menu"  # required, specify menu_id where this item will go
        self.menu_name = _("EELS")  # optional, specify default name if not a standard menu
        self.menu_before_id = "window_menu"  # optional, specify before menu_id if not a standard menu
        self.menu_item_name = _("[EXPERIMENTAL] Align SI sequence")  # menu item name

    def menu_item_execute(self, window: Facade.DocumentWindow) -> None:
        try:
            selected_display_items = window._document_controller._get_two_data_sources()
            error_msg = "Select a sequence of spectrum images and a sequence of scanned images in order to use this computation."
            assert selected_display_items[0][0] is not None, error_msg
            assert selected_display_items[1][0] is not None, error_msg
            assert selected_display_items[0][0].data_item is not None, error_msg
            assert selected_display_items[1][0].data_item is not None, error_msg
            assert selected_display_items[0][0].data_item.is_sequence, error_msg
            assert selected_display_items[1][0].data_item.is_sequence, error_msg
            assert selected_display_items[1][0] != selected_display_items[0][0], error_msg

            if selected_display_items[0][0].data_item.is_collection:
                si_sequence_data_item = Facade.DataItem(selected_display_items[0][0].data_item)
                haadf_sequence_data_item = Facade.DataItem(selected_display_items[1][0].data_item)
                align_region = Facade.Graphic(selected_display_items[1][1]) if selected_display_items[1][1] else None
            elif selected_display_items[1][0].data_item.is_collection:
                si_sequence_data_item = Facade.DataItem(selected_display_items[1][0].data_item)
                haadf_sequence_data_item = Facade.DataItem(selected_display_items[0][0].data_item)
                align_region = Facade.Graphic(selected_display_items[0][1]) if selected_display_items[0][1] else None
            else:
                raise ValueError(error_msg)

            bounds_graphic = align_region if align_region and align_region.graphic_type == "rect-graphic" else None

            align_multi_si2(self.__api, window, haadf_sequence_data_item, bounds_graphic, si_sequence_data_item)
        except Exception as e:
            import traceback
            traceback.print_exc()
            from nion.swift.model import Notification
            Notification.notify(Notification.Notification("nion.computation.error", "\N{WARNING SIGN} Computation", "Align sequence of SI failed", str(e)))


class AlignMultiSIExtension:

    # required for Swift to recognize this as an extension class.
    extension_id = "nion.experimental.align_multi_si"

    def __init__(self, api_broker: typing.Any) -> None:
        # grab the api object.
        api = api_broker.get_api(version="~1.0")
        self.__align_multi_si_menu_item_ref = api.create_menu_item(AlignMultiSIMenuItemDelegate(api))

    def close(self) -> None:
        # close will be called when the extension is unloaded. in turn, close any references so they get closed. this
        # is not strictly necessary since the references will be deleted naturally when this object is deleted.
        self.__align_multi_si_menu_item_ref.close()
        self.__align_multi_si_menu_item_ref = None
