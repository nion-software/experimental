# mypy --namespace-packages --ignore-missing-imports --follow-imports=silent --strict --no-warn-unused-ignores --exclude '/test' --exclude '/docs' -p nionswift_plugin.drift_correction

from __future__ import annotations
import typing
import gettext
import threading
import time
import warnings
import logging
import asyncio
import pkgutil
import os
import subprocess
import json
import urllib.request
import ctypes

import numpy
import numpy.typing
import scipy.ndimage

from nion.utils import Registry
from nion.utils import Event
from nion.utils import Geometry
from nion.data import xdata_1_0 as xd
from nion.data import Core
from nion.typeshed import API_1_0
from nion.ui import Declarative
from nion.swift.model import PlugInManager
from nion.utils import Converter
from nion.swift import Panel
from nion.swift import DocumentController
from nion.swift import Workspace
from nion.ui import CanvasItem

from . import settings_dialog

_NDArray = numpy.typing.NDArray[typing.Any]


_ = gettext.gettext


# Note: If you change those control names, you should also update the documentation to reflect the changes!

DRIFT_VECTOR_CONTROL = 'DriftCompensation' # AS2 (2d) control that contains the drift vector in m/s
DRIFT_RATE_CONTROL = 'DriftRate' # AS2 (2d) control that contains the last measured drift rate. To actually apply the
                                 # measured drift you have to use "zero ouptput" on this control which wil cause the
                                 # measured drift rate to be added to DRIFT_VECTOR_CONTROL if they are linked properly
TUNING_DRIFT_RATE_CONTROL = 'Drift' # AS2 (2d) control in which tuning saves the measured drift
SHIFTER_CONTROL = 'CSH' # AS2 probe shifter control
STAGE_CONTROL = 'SShft'
AS2_UPDATE_INTERVAL = 1.0 # minimum interval we update from as2 in s
AS2_PORT = None # Set this to a port to make the plugin use it. Otherwise it will try to find a working one automatically


def calculate_image_moments(image: _NDArray) -> typing.Tuple[float, float, float, float, float, float]:
    """
    Calculates the centralized image moments up to 2nd order and the parameters of an ellipse with the
    same moments.

    Returns
    --------
    sum : float
          Sum of all pixels of the input image.
    center_y : float
               Y-coordinate of the image centroid.
    center_x : float
               X-coordinate of the image centroid.
    a : float
        Length of the major half-axis of an ellipse with the same moments as the input image.
    b : float
        Length of the minor half-axis of an ellipse with the same moments as the input image.
    angle : float
            Angle of an ellipse with the same moments as the input image.
    """
    coords_y: numpy.typing.NDArray[numpy.float64] = numpy.arange(image.shape[0], dtype=float)
    coords_x: numpy.typing.NDArray[numpy.float64] = numpy.arange(image.shape[1], dtype=float)

    proj_y = numpy.sum(image, axis=1)
    proj_x = numpy.sum(image, axis=0)

    nu00 = numpy.sum(proj_y)
    mu01 = numpy.sum(coords_y*proj_y)/nu00
    mu10 = numpy.sum(coords_x*proj_x)/nu00
    coords_y -= mu01
    coords_x -= mu10
    nu11 = numpy.sum(coords_y.reshape((-1, 1))*coords_x*image)/nu00
    nu02 = numpy.sum(coords_y**2*proj_y)/nu00
    nu20 = numpy.sum(coords_x**2*proj_x)/nu00
    #nu04 = numpy.sum(coords[0]**4*image)/nu00
    #nu40 = numpy.sum(coords[1]**4*image)/nu00
    # Find image orientation
    # Formula taken from https://en.wikipedia.org/wiki/Image_moment
    covmat: numpy.typing.NDArray[numpy.float64] = numpy.array(((nu20, nu11), (nu11, nu02)))
    eigval, eigvec = numpy.linalg.eig(covmat) # type: ignore
    angle_vec = eigvec[:, numpy.argmax(eigval)]
    angle = numpy.arctan2(-1.0*angle_vec[1], angle_vec[0])
    #excent = numpy.sqrt(1-numpy.amin(eigval)**2/numpy.amax(eigval)**2)
    # Standard deviation in polar coordinates
    # Formula taken from http://stackoverflow.com/questions/13894631/image-skewness-kurtosis-in-python
    #stddev_mag = numpy.sqrt(nu02 + nu20)
    #stddev_angle = numpy.arctan2(numpy.sqrt(nu02), numpy.sqrt(nu20))
    # Kurtosis in polar coordinates
    #kurtosis_mag = numpy.sqrt(nu04**2/nu02**4 + nu40**2/nu20**4)
    #kurtosis_angle = numpy.arctan2(nu04/nu02**2, nu40/nu20**2)

    # the half-axis lengths of the corresponding ellipse are 2*sqrt(eigenvalue)
    return float(nu00), float(mu01), float(mu10), float(2.0 * numpy.sqrt(numpy.amax(eigval))), float(2.0 * numpy.sqrt(numpy.amin(eigval))), float(angle)


def make_binary_image(image: _NDArray, *, blur_radius: float=2, threshold: typing.Optional[float]=None,
                      **kwargs: typing.Any) -> _NDArray:
    """
    Converts an image to a binary mask via thresholding. If `blur_radius` is > 0, a Gaussian filter will be applied
    to the image before thresholding. If `threshold` is `None`, a threshold will be found by calling `auto threshold`.
    Returns a binary mask where brigth areas in the input image are marked with 1 and dark areas with 0.
    """
    if blur_radius > 0:
        blurred_image = scipy.ndimage.gaussian_filter(image, blur_radius)
    else:
        blurred_image = image
    if threshold is None:
        t0 = time.perf_counter()
        threshold = Core.auto_threshold(blurred_image, **kwargs)
    # use int8 instead of uint8 because otherwise Swift goes crazy when displaying the mask
    # (because it expects rgb data)
    binary_image: numpy.typing.NDArray[numpy.int8] = numpy.zeros(blurred_image.shape, dtype=numpy.int8)
    binary_image[blurred_image>=threshold] = 1
    return binary_image


class DriftCorrectionSettings:
    _settings_dialog_ui_elements = [
        {'property_name': 'update_interval', 'display_name': 'Shifter update interval (s)', 'ui_element': 'line_edit', 'value_type': 'float',
         'description': 'Interval for the shifter control updates.'},
        {'property_name': 'measure_sleep_time', 'display_name': 'Measure drift wait time (s)', 'ui_element': 'line_edit', 'value_type': 'float',
         'description': 'Time to wait between the two camera frames that are used to calculate the current drift rate.'},
        {'property_name': 'drift_time_constant', 'display_name': 'Drift time constant (min)', 'ui_element': 'line_edit', 'value_type': 'float',
         'description': 'Time constant for the drift. The used drift rate will be decreased with this time constant.', 'multiplier': 60},
        {'property_name': 'max_shifter_range', 'display_name': 'Maximum shifter range (nm)', 'ui_element': 'line_edit', 'value_type': 'float', 'multiplier': 1e-9,
         'description': 'Maximum range for the beam shifters.'},
        {'property_name': 'auto_stop_threshold', 'display_name': 'Auto stop threshold', 'ui_element': 'line_edit', 'value_type': 'float',
         'description': 'Stop drift correction after moving further than this times the maximum shifter range.'},
        {'property_name': 'reset_shifters_to_opposite', 'display_name': 'Reset shifters to opposite', 'ui_element': 'check_box', 'value_type': 'bool',
         'description': 'Reset shifters to their opposite when clicking "Reset". This will extend the usable range for drift correction by a factor of 2.'}]

    def __init__(self) -> None:
        self.update_interval = 0.1 # in seconds
        self.measure_sleep_time = 10 # in seconds
        self.ccorr_threshold = 0.4
        self.min_patch_radius = 64
        self.max_patch_radius = 512
        self.drift_time_constant = 40*60 # = 40 min
        self.max_shifter_range = 100e-9
        self.auto_stop_threshold = 2.0 # Stop drift correction after moving further than this times the max_shifter_range
        self.reset_shifters_to_opposite = False # When resetting shifters, set them to the opposite of what they were and compensate with stage (gives more correction range)

    @classmethod
    def from_dict(cls, settings_dict: typing.Mapping[str, typing.Any]) -> 'DriftCorrectionSettings':
        tuning_settings = cls()
        for key, value in settings_dict.items():
            if hasattr(tuning_settings, key):
                setattr(tuning_settings, key, value)
        return tuning_settings

    @property
    def _as2_names(self) -> typing.Mapping[str, str]:
        return {'update_interval': 'DriftCorrectionUpdateInterval',
                'measure_sleep_time': 'DriftMeasureTime',
                'ccorr_threshold': 'DriftCcorrThreshold',
                'drift_time_constant': 'DriftTimeConstant',
                'max_shifter_range': 'MaxShifterRange',
                'auto_stop_threshold': 'DiftAutoStopThreshold',
                'reset_shifters_to_opposite': 'ResetShiftersToOpposite'}

    def to_dict(self) -> typing.Mapping[str, typing.Any]:
        return {'update_interval': self.update_interval,
                'measure_sleep_time': self.measure_sleep_time,
                'ccorr_threshold': self.ccorr_threshold,
                'drift_time_constant': self.drift_time_constant,
                'max_shifter_range': self.max_shifter_range,
                'min_patch_radius': self.min_patch_radius,
                'max_patch_radius': self.max_patch_radius,
                'auto_stop_threshold': self.auto_stop_threshold,
                'reset_shifters_to_opposite': self.reset_shifters_to_opposite}

    def save_to_as2(self, stem_controller: typing.Any, property_name: str) -> bool:
        as2_name = self._as2_names.get(property_name)
        if hasattr(self, property_name) and as2_name:
            success = stem_controller.SetVal(as2_name, getattr(self, property_name))
            if not success:
                logging.debug(f'Unable to set parameter {as2_name} to {getattr(self, property_name)} in AS2.')
            return bool(success)
        return False

    def update_from_as2(self, stem_controller: typing.Any) -> None:
        for key, value in self._as2_names.items():
            success, result = stem_controller.TryGetVal(value)
            if not success:
                logging.debug(f'Unable to get parameter {value} from AS2.')
            if success:
                setattr(self, key, result)


class DriftCorrectionUIHandler(Declarative.Handler):

    def __init__(self, api: API_1_0, drift_corrector: DriftCorrector, stem_controller: typing.Any, ui_view: typing.Mapping[str, typing.Any], event_loop: asyncio.AbstractEventLoop):
        super().__init__()
        self.__api = api
        self.__event_loop = event_loop
        self.__drift_corrector = drift_corrector
        self.__stem_controller = stem_controller
        self.ui_view = ui_view
        self.property_changed_event = Event.Event()
        self.modes = ['auto', 'manual']

        self.__status_label_text = ''
        self.__progress_bar_label_text = ''
        self.__drift_rate_line_edit_enabled = True
        self.__ui_enabled = True
        self.__progress_bar_value = 0.0
        self.__last_status_update = (0.0, 100)

        def property_changed(name: str) -> None:
            if name == 'drift_vector':
                self.property_changed_event.fire('drift_rate_x')
                self.property_changed_event.fire('drift_rate_y')
            if name == 'enabled':
                self.property_changed_event.fire('enabled')

        self.__drift_corrector_property_changed_event_listener = self.__drift_corrector.property_changed_event.listen(property_changed)
        self.__drift_corrector_status_changed_event_listener = self.__drift_corrector.status_updated_event.listen(self.handle_status_message)
        self.__drift_corrector_progress_changed_event_listener = self.__drift_corrector.progress_updated_event.listen(lambda progress: setattr(self, 'progress_bar_value', progress))
        self.__settings_dialog_open = False
        self.drift_rate_converter = Converter.PhysicalValueToStringConverter('nm/min', multiplier=1e9*60, format='{:.4g}')
        logo_data = pkgutil.get_data(__name__, "resources/sliders_icon_24.png")
        assert logo_data is not None
        self.settings_icon = CanvasItem.load_rgba_data_from_bytes(logo_data, "png")
        logo_data = pkgutil.get_data(__name__, "resources/help_icon_24.png")
        assert logo_data is not None
        self.help_icon = CanvasItem.load_rgba_data_from_bytes(logo_data, "png")

    def init_handler(self) -> None:
        self.__drift_corrector.start()

    def close(self) -> None:
        self.__drift_corrector.close()
        if self.__drift_corrector_property_changed_event_listener:
            self.__drift_corrector_property_changed_event_listener.close()
        self.__drift_corrector_property_changed_event_listener = typing.cast(typing.Any, None)
        if self.__drift_corrector_status_changed_event_listener:
            self.__drift_corrector_status_changed_event_listener.close()
        self.__drift_corrector_status_changed_event_listener = typing.cast(typing.Any, None)
        if self.__drift_corrector_progress_changed_event_listener:
            self.__drift_corrector_progress_changed_event_listener.close()
        self.__drift_corrector_progress_changed_event_listener = typing.cast(typing.Any, None)
        super().close()

    @property
    def enabled(self) -> bool:
        return self.__drift_corrector.enabled

    @enabled.setter
    def enabled(self, enabled: bool) -> None:
        self.__drift_corrector.enabled = enabled
        self.progress_bar_label_text = f'Used shifter range ({self.__drift_corrector.settings.drift_time_constant/60.0:.0f} min time constant)'
        self.property_changed_event.fire('enabled')
        self.property_changed_event.fire('not_enabled')
        self.property_changed_event.fire('drift_rate_line_edit_enabled')

    @property
    def not_enabled(self) -> bool:
        return not self.enabled

    @not_enabled.setter
    def not_enabled(self, not_enabled: bool) -> None:
        self.enabled = not not_enabled

    @property
    def status_label_text(self) -> str:
        return self.__status_label_text

    @status_label_text.setter
    def status_label_text(self, text: str) -> None:
        self.__status_label_text = text
        self.property_changed_event.fire('status_label_text')

    @property
    def progress_bar_label_text(self) -> str:
        return self.__progress_bar_label_text

    @progress_bar_label_text.setter
    def progress_bar_label_text(self, text: str) -> None:
        self.__progress_bar_label_text = text
        self.property_changed_event.fire('progress_bar_label_text')

    @property
    def drift_rate_x(self) -> float:
        return float(self.__drift_corrector.drift_vector[0])

    @drift_rate_x.setter
    def drift_rate_x(self, drift_rate: float) -> None:
        self.__drift_corrector.drift_vector[0] = drift_rate
        self.property_changed_event.fire('drift_rate_x')

    @property
    def drift_rate_y(self) -> float:
        return float(self.__drift_corrector.drift_vector[1])

    @drift_rate_y.setter
    def drift_rate_y(self, drift_rate: float) -> None:
        self.__drift_corrector.drift_vector[1] = drift_rate
        self.property_changed_event.fire('drift_rate_y')

    @property
    def mode(self) -> str:
        return self.__drift_corrector.mode

    @mode.setter
    def mode(self, mode: str) -> None:
        self.__drift_corrector.mode = mode
        self.property_changed_event.fire('drift_rate_line_edit_enabled')
        self.property_changed_event.fire('adjust_button_enabled')

    @property
    def mode_index(self) -> int:
        return self.modes.index(self.mode)

    @mode_index.setter
    def mode_index(self, index: int) -> None:
        self.mode = self.modes[index]
        self.property_changed_event.fire('mode_index')
        
    @property
    def axis_names(self) -> typing.List[str]:
        return list(self.__drift_corrector._axis_name_map.keys())

    @property
    def axis_name_index(self) -> int:
        return list(self.__drift_corrector._axis_name_map.values()).index(self.__drift_corrector.axis)

    @axis_name_index.setter
    def axis_name_index(self, index: int) -> None:
        self.__drift_corrector.axis = list(self.__drift_corrector._axis_name_map.values())[index]
        self.property_changed_event.fire('axis_name_index')
        self.property_changed_event.fire('x_axis_name')
        self.property_changed_event.fire('y_axis_name')

    @property
    def x_axis_name(self) -> str:
        return self.__drift_corrector.axis[0].upper() + ': '

    @property
    def y_axis_name(self) -> str:
        return self.__drift_corrector.axis[1].upper() + ': '

    @property
    def ui_enabled(self) -> bool:
        return self.__ui_enabled

    @ui_enabled.setter
    def ui_enabled(self, enabled: bool) -> None:
        self.__ui_enabled = enabled
        self.property_changed_event.fire('ui_enabled')
        self.property_changed_event.fire('drift_rate_line_edit_enabled')

    @property
    def drift_rate_line_edit_enabled(self) -> bool:
        return self.__ui_enabled and not self.enabled and self.mode == 'manual'

    @drift_rate_line_edit_enabled.setter
    def drift_rate_line_edit_enabled(self, enabled: bool) -> None:
        ...

    @property
    def adjust_button_enabled(self) -> bool:
        return self.mode == 'manual'

    @adjust_button_enabled.setter
    def adjust_button_enabled(self, enabled: bool) -> None:
        ...

    @property
    def progress_bar_value(self) -> float:
        return self.__progress_bar_value

    @progress_bar_value.setter
    def progress_bar_value(self, progress: float) -> None:
        self.__progress_bar_value = int(progress)
        self.property_changed_event.fire('progress_bar_value')

    def handle_status_message(self, message: str, priority: int=100) -> None:
        now = time.time()
        if now - self.__last_status_update[0] > 2.0 or priority >= self.__last_status_update[1]:
            self.status_label_text = message
            self.__last_status_update = (now, priority)

    def measure_drift_clicked(self, widget: Declarative.UIWidget) -> None:
        self.ui_enabled = False
        def run_measure() -> None:
            try:
                self.__drift_corrector.measure_drift()
            except:
                import traceback
                traceback.print_exc()
            finally:
                self.ui_enabled = True

        threading.Thread(target=run_measure, daemon=True).start()

    def settings_clicked(self, widget: Declarative.UIWidget) -> typing.Optional[Declarative.UIWidget]:
        if self.__settings_dialog_open:
            return None

        document_controller = self.__api.application.document_controllers[0]._document_controller
        ui_handler = settings_dialog.SettingsUI().get_ui_handler(self.__drift_corrector.settings,
                                                                 api_broker=PlugInManager.APIBroker(),
                                                                 stem_controller=self.__stem_controller,
                                                                 title='Drift correction settings')
        setattr(ui_handler, 'drift_corrector_state_changed_event_listener', self.__drift_corrector.drift_corrector_state_changed_event.listen(ui_handler.update_ui))

        assert ui_handler.ui_view is not None
        dialog = Declarative.construct(document_controller.ui, document_controller, ui_handler.ui_view, ui_handler)

        def wc(w: typing.Any) -> None:
            self.__settings_dialog_open = False
            getattr(ui_handler, 'configuration_dialog_close_listener').close()
            delattr(ui_handler, 'configuration_dialog_close_listener')
            getattr(ui_handler, 'drift_corrector_state_changed_event_listener').close()
            delattr(ui_handler, 'drift_corrector_state_changed_event_listener')

        # use set handler to pass type checking.
        setattr(ui_handler, 'configuration_dialog_close_listener', typing.cast(typing.Any, dialog)._window_close_event.listen(wc))

        ui_handler.init_handler()

        typing.cast(typing.Any, dialog).show()
        self.__settings_dialog_open = True

        # Return the dialog which is useful for testing
        return dialog

    def use_tuning_drift_clicked(self, widget: Declarative.UIWidget) -> None:
        self.__drift_corrector.use_tuning_drift()

    def use_measured_drift_clicked(self, widget: Declarative.UIWidget) -> None:
        self.__drift_corrector.use_measured_drift()

    def reset_shifters_clicked(self, widget: Declarative.UIWidget) -> None:
        self.__drift_corrector.reset_shifters()

    def help_clicked(self, widget: Declarative.UIWidget) -> None:
        docs_path = os.path.join(os.path.dirname(__file__), 'resources', 'html', 'index.html')
        logging.info(f'Trying to display help from: {docs_path}')
        subprocess.call('start ' + docs_path, shell=True)

    def manual_adjust_drift_rate_clicked(self, widget: Declarative.UIWidget) -> None:
        global AS2_PORT
        wait_event = threading.Event()
        def listen(state: typing.Mapping[str, str]) -> None:
            if state.get('manual_drift_adjustment') == 'enabled':
                wait_event.set()
        listener = self.__drift_corrector.drift_corrector_state_changed_event.listen(listen)
        self.__drift_corrector.start_manual_drift_adjustment()
        wait_event.wait(1.0)
        listener.close()
        widget_coords = widget.map_to_global(Geometry.IntPoint.make((widget.size * 0.5).as_point()))
        if AS2_PORT is None: # type: ignore
            AS2_PORT = _find_correct_port()
        if AS2_PORT is None:
            logging.error('Could not find a working port for talking to AS2. Cannot adjust drift rate.')
            self.__drift_corrector.end_manual_drift_adjustment()
            return
        display_scaling = self.__api.application.document_controllers[0]._document_window.display_scaling
        # We need to allow AS2 to set the foreground window, otherwise the popupedit will get closed again immediately
        # or not work properly. Since this code only works on Windows, enclose it in a try...except block. We also
        # need to disable typing because type checks will also fail on non-windows systems.
        try:
            ctypes.windll.user32.AllowSetForegroundWindow(-1) # type: ignore
        except AttributeError:
            import traceback
            traceback.print_exc()
        result, message = query(('ui', 'popupedit'), value={'Elements': [DRIFT_VECTOR_CONTROL + '.' + self.__drift_corrector.axis[0],
                                                                         DRIFT_VECTOR_CONTROL + '.' + self.__drift_corrector.axis[1]],
                                                            'Visible': True, 'X': widget_coords.x * display_scaling,
                                                            'Y': widget_coords.y * display_scaling})
        if message:
            logging.error(message)
            self.__drift_corrector.end_manual_drift_adjustment()
            return

        def wait_for_edit_finished() -> None:
            result, message = query(('ui', 'popupedit'))
            if message or not result.get('Visible'):
                self.__drift_corrector.end_manual_drift_adjustment()
            else:
                self.__event_loop.call_later(0.1, wait_for_edit_finished)

        self.__event_loop.call_later(0.1, wait_for_edit_finished)


class DriftCorrectionUI:

    def get_ui_handler(self, stem_controller: typing.Any, api_broker: PlugInManager.APIBroker, event_loop: asyncio.AbstractEventLoop, **kwargs: typing.Any) -> DriftCorrectionUIHandler:
        api = api_broker.get_api('~1.0')
        ui = api_broker.get_ui('~1.0')
        ui_view = self.__create_ui_view(ui)
        drift_corrector = DriftCorrector(stem_controller, DriftCorrectionSettings())
        return DriftCorrectionUIHandler(api, drift_corrector, stem_controller, ui_view, event_loop)

    def __create_ui_view(self, ui: Declarative.DeclarativeUI) -> typing.Mapping[str, typing.Any]:
        row1 = ui.create_row(ui.create_check_box(text='Enable', checked='@binding(enabled)'),
                             ui.create_spacing(10),
                             ui.create_label(text='Mode: '),
                             ui.create_combo_box(items_ref='modes', current_index='@binding(mode_index)'),
                             ui.create_spacing(10), ui.create_stretch(),
                             ui.create_push_button(text='Measure drift', on_clicked='measure_drift_clicked'),
                             ui.create_spacing(10),
                             ui.create_push_button(icon='help_icon', on_clicked='help_clicked', width=23, height=23),
                             ui.create_spacing(5),
                             ui.create_push_button(icon='settings_icon', on_clicked='settings_clicked', width=23, height=23),
                             margin=5, enabled='@binding(ui_enabled)')
        row2 = ui.create_row(ui.create_label(text='Axis: '),
                             ui.create_combo_box(items_ref='axis_names', current_index='@binding(axis_name_index)', enabled='@binding(not_enabled)'),
                             ui.create_stretch(),
                             margin=5, enabled='@binding(ui_enabled)')
        row3 = ui.create_row(ui.create_label(text='@binding(x_axis_name)'),
                             ui.create_line_edit(text='@binding(drift_rate_x, converter=drift_rate_converter)', enabled='@binding(drift_rate_line_edit_enabled)', width=80),
                             ui.create_stretch(),
                             ui.create_spacing(10),
                             ui.create_push_button(text='Use measured drift', on_clicked='use_measured_drift_clicked'),
                             ui.create_spacing(10),
                             ui.create_push_button(text='Use tuning drift', on_clicked='use_tuning_drift_clicked'),
                             margin=5, enabled='@binding(ui_enabled)')

        row4 = ui.create_row(ui.create_label(text='@binding(y_axis_name)'),
                             ui.create_line_edit(text='@binding(drift_rate_y, converter=drift_rate_converter)', enabled='@binding(drift_rate_line_edit_enabled)', width=80),
                             ui.create_spacing(5),
                             ui.create_push_button(text='Adjust', on_clicked='manual_adjust_drift_rate_clicked', enabled='@binding(adjust_button_enabled)', width=60),
                             ui.create_stretch(),
                             ui.create_spacing(10),
                             ui.create_push_button(text='Reset shifters', on_clicked='reset_shifters_clicked'),
                             margin=5, enabled='@binding(ui_enabled)')

        row5 = ui.create_row(ui.create_label(text='@binding(progress_bar_label_text)'),
                             ui.create_spacing(10),
                             ui.create_progress_bar(value='@binding(progress_bar_value)', width=120),
                             margin=5)

        row6 = ui.create_row(ui.create_label(text='@binding(status_label_text)'),
                             ui.create_stretch(),
                             margin=5)

        return ui.create_column(row1, row2, row3, row4, row5, row6, ui.create_stretch(), margin=5)


class DriftCorrector:
    _axis_name_map = {'TV': ('x', 'y'), 'Scan': ('u', 'v')}

    def __init__(self, stem_controller: typing.Any, settings: DriftCorrectionSettings):
        self.__stem_controller = stem_controller
        self.__settings = settings
        self.__lock = threading.Lock()
        self.__queue: typing.Dict[str, typing.Any] = dict()
        self.status_updated_event = Event.Event()
        self.progress_updated_event = Event.Event()
        self.property_changed_event = Event.Event()
        self.drift_corrector_state_changed_event = Event.Event()

        self.__last_as2_update = 0.0
        # This is interpreted in x, y order!
        self.__drift_vector: numpy.typing.NDArray[typing.Any] = numpy.array((0.0, 0.0))
        self.__last_update = time.time()
        self.__drift_vector_backup: typing.Optional[_NDArray] = None
        self.__as2_update_rate_backup: typing.Optional[float] = None

        self.__enabled = False
        self.__decrease_drift_rate = True
        self.mode = 'auto'
        self.axis = ('x', 'y')

        self.__thread: typing.Optional[threading.Thread] = None
        self.__stop_event = threading.Event()

    def start(self) -> None:
        self.__thread = threading.Thread(target=self.correction_loop, daemon=True)
        self.__thread.start()

    def close(self) -> None:
        self.__stop_event.set()

    @property
    def settings(self) -> DriftCorrectionSettings:
        return self.__settings

    @property
    def enabled(self) -> bool:
        return self.__enabled

    @enabled.setter
    def enabled(self, enabled: bool) -> None:
        self.settings.update_from_as2(self.__stem_controller)
        if enabled:
            self.status_updated_event.fire('Enabled')
            self.drift_corrector_state_changed_event.fire({'state': 'running'})
            self.__last_update = time.time()
        else:
            self.status_updated_event.fire('Disabled')
            self.drift_corrector_state_changed_event.fire({'state': 'disabled'})
        self.__enabled = enabled
        self.property_changed_event.fire('enabled')

    @property
    def drift_vector(self) -> _NDArray:
        return self.__drift_vector

    @property
    def decrease_drift_rate(self) -> bool:
        return self.__decrease_drift_rate

    @decrease_drift_rate.setter
    def decrease_drift_rate(self, decrease: bool) -> None:
        self.__decrease_drift_rate = decrease
        self.property_changed_event.fire('decrease_drift_rate')

    def get_vector(self, control_name: str, axis: typing.Optional[typing.Tuple[str, str]]=None) -> typing.Tuple[bool, _NDArray]:
        if axis is None:
            axis = self.axis
        success_a, vector_a = self.__stem_controller.TryGetVal(control_name + '.' + axis[0])
        success_b, vector_b = self.__stem_controller.TryGetVal(control_name + '.' + axis[1])
        return success_a & success_b, numpy.array((vector_a, vector_b))

    def set_vector(self, control_name: str, value: typing.Union[_NDArray, typing.Tuple[float, float]], axis: typing.Optional[typing.Tuple[str, str]]=None, confirm: bool=False) -> bool:
        if axis is None:
            axis = self.axis
        if confirm:
            # We only need to confirm the second direction because commands get sent out one after the other.
            success_a = self.__stem_controller.SetVal(control_name + '.' + axis[0], float(value[0]))
            success_b = self.__stem_controller.SetValAndConfirm(control_name + '.' + axis[1], float(value[1]), 1.0, 3000)
        else:
            success_a = self.__stem_controller.SetVal(control_name + '.' + axis[0], float(value[0]))
            success_b = self.__stem_controller.SetVal(control_name + '.' + axis[1], float(value[1]))
        return bool(success_a & success_b)

    def update_vector(self, control_name: str, value: _NDArray, axis: typing.Optional[typing.Tuple[str, str]]=None, confirm: bool=False) -> bool:
        if axis is None:
            axis = self.axis
        success, current = self.get_vector(control_name, axis=axis)
        if success:
            success = self.set_vector(control_name, current + value, axis=axis, confirm=confirm)
        return success

    def inform_vector(self, control_name: str, value: _NDArray, axis: typing.Optional[typing.Tuple[str, str]]=None) -> bool:
        if axis is None:
            axis = self.axis
        success_a = self.__stem_controller.InformControl(control_name + '.' + axis[0], float(value[0]))
        success_b = self.__stem_controller.InformControl(control_name + '.' + axis[1], float(value[1]))
        return bool(success_a & success_b)

    def update_from_as2(self) -> bool:
        success = True
        now = time.time()
        if now - self.__last_as2_update > AS2_UPDATE_INTERVAL:
            success, drift_vector = self.get_vector(DRIFT_VECTOR_CONTROL)
            if success:
                self.__drift_vector = drift_vector
                self.property_changed_event.fire('drift_vector')
            else:
                self.status_updated_event.fire(f'Failed to get drift vector from AS2 ({DRIFT_VECTOR_CONTROL})')
            self.__last_as2_update = now
        return success

    def use_tuning_drift(self) -> None:
        def do_update() -> None:
            success, tuning_drift = self.get_vector(TUNING_DRIFT_RATE_CONTROL)
            if success:
                self.__last_as2_update = 0.0
                if self.enabled:
                    self.update_vector(DRIFT_VECTOR_CONTROL, -1.0 * tuning_drift)
                else:
                    self.set_vector(DRIFT_VECTOR_CONTROL, -1.0 * tuning_drift)
                self.status_updated_event.fire(f'Using tuning drift (x, y): ({tuning_drift[0]:.3g}, {tuning_drift[1]:.3g}) m/s.')
                self.update_from_as2()
            else:
                self.status_updated_event.fire(f'Failed to get tuning drift rate from {TUNING_DRIFT_RATE_CONTROL}.')

        with self.__lock:
            self.__queue['update_drift'] = do_update

    def use_measured_drift(self) -> None:
        def do_update() -> None:
            self.__last_as2_update = 0.0
            self.set_vector(DRIFT_RATE_CONTROL, numpy.array((0, 0)))
            self.update_from_as2()

        with self.__lock:
            self.__queue['update_drift'] = do_update

    def start_manual_drift_adjustment(self) -> None:
        def do_update() -> None:
            global AS2_UPDATE_INTERVAL
            self.decrease_drift_rate = False
            _, self.__drift_vector_backup = self.get_vector(DRIFT_VECTOR_CONTROL)
            self.__as2_update_rate_backup = AS2_UPDATE_INTERVAL
            AS2_UPDATE_INTERVAL = 0
            self.set_vector(DRIFT_VECTOR_CONTROL, self.__drift_vector)
            self.drift_corrector_state_changed_event.fire({'manual_drift_adjustment': 'enabled'})

        with self.__lock:
            self.__queue['start_manual'] = do_update

    def end_manual_drift_adjustment(self) -> None:
        def do_update() -> None:
            global AS2_UPDATE_INTERVAL
            self.decrease_drift_rate = True
            _, self.__drift_vector = self.get_vector(DRIFT_VECTOR_CONTROL)
            if self.__drift_vector_backup is not None:
                self.set_vector(DRIFT_VECTOR_CONTROL, self.__drift_vector_backup)
            if self.__as2_update_rate_backup is not None:
                AS2_UPDATE_INTERVAL = self.__as2_update_rate_backup
            self.__drift_vector_backup = None
            self.__as2_update_rate_backup = None
            self.drift_corrector_state_changed_event.fire({'manual_drift_adjustment': 'disabled'})

        with self.__lock:
            self.__queue['end_manual'] = do_update

    def update_progress(self) -> None:
        success, value = self.get_vector(SHIFTER_CONTROL)
        if success:
            progress = numpy.amax(numpy.abs(value)) / self.settings.max_shifter_range * 100.0
            remaining_shifter_range = self.settings.max_shifter_range - numpy.abs(value)
            if (remaining_shifter_range < self.settings.drift_time_constant * numpy.abs(self.__drift_vector)).any():
                with warnings.catch_warnings():
                    warnings.simplefilter('ignore')
                    remaining_time = numpy.nanmin(-numpy.log(1 - remaining_shifter_range / self.settings.drift_time_constant / numpy.abs(self.__drift_vector)) * self.settings.drift_time_constant) # type: ignore
                self.progress_updated_event.fire(progress)
                if self.enabled:
                    if remaining_time > 0:
                        self.status_updated_event.fire(f'Remaining Time: {remaining_time:.0f} s', priority=10)
                    else:
                        shifter_excess = numpy.abs(numpy.amin(remaining_shifter_range) / self.settings.max_shifter_range) + 1
                        if shifter_excess > self.settings.auto_stop_threshold:
                            self.enabled = False
                            self.status_updated_event.fire(f'Disabled drift correction because shifter range was exceeded by more than {self.settings.auto_stop_threshold - 1:.0%}!')
                        else:
                            self.status_updated_event.fire(f'Exceeded maximum shifter range by {shifter_excess - 1:.0%}!', priority=20)
                else:
                    self.status_updated_event.fire('Remaining Time: --')

    def reset_shifters(self, compensate: bool=True) -> None:
        def do_update() -> None:
            success, value = self.get_vector(SHIFTER_CONTROL)
            if compensate:
                if success:
                    if self.settings.reset_shifters_to_opposite:
                        self.update_vector(STAGE_CONTROL, -2.0*value, confirm=True)
                    else:
                        self.update_vector(STAGE_CONTROL, -1.0*value, confirm=True)
            if self.settings.reset_shifters_to_opposite:
                if success:
                    success = self.set_vector(SHIFTER_CONTROL, -1.0*value, confirm=True)
            else:
                success = self.set_vector(SHIFTER_CONTROL, numpy.array((0, 0)), confirm=True)
            if success:
                self.update_progress()
        with self.__lock:
            self.__queue['reset_shifters'] = do_update

    def correction_loop(self) -> None:
        while not self.__stop_event.is_set():
            start_time = time.time()
            if self.enabled:
                # If as2_upadate_rate_backup is not None we are adjusting the drift rate via an AS2 popup. In this case
                # we also need to update from AS2 to make the changed drift rate apply live
                if self.mode == 'auto' or self.__as2_update_rate_backup is not None:
                    success = self.update_from_as2()
                else:
                    success = True
                now = time.time()
                time_difference = now - self.__last_update
                if success:
                    correction_vector: _NDArray = time_difference * self.__drift_vector
                    correction_success = self.update_vector(SHIFTER_CONTROL, correction_vector, confirm=True)
                    if correction_success:
                        self.update_progress()
                    else:
                        self.status_updated_event.fire(f'Failed to set shifters ({SHIFTER_CONTROL})')
                if self.decrease_drift_rate:
                    self.__drift_vector = self.__drift_vector * (1 - time_difference / self.settings.drift_time_constant)
                    self.property_changed_event.fire('drift_vector')
                if self.mode == 'auto' and self.decrease_drift_rate:
                    self.set_vector(DRIFT_VECTOR_CONTROL, self.__drift_vector)
                self.__last_update = now

            with self.__lock:
                task_list = list(self.__queue.values())
                self.__queue.clear()

            for task in task_list:
                try:
                    task()
                except:
                    import traceback
                    traceback.print_exc()

            now = time.time()
            time.sleep(max(0.0, self.settings.update_interval - (now - start_time)))

    def measure_drift_camera(self) -> None:
        success, defocus = self.__stem_controller.TryGetVal('C10')
        if not success:
            self.status_updated_event.fire('Failed to get defocus from AS2.')
            return
        camera = self.__stem_controller.ronchigram_camera
        start_image = camera.grab_next_to_start()[0]
        start_time = time.time()
        self.status_updated_event.fire(f'Waiting for {self.settings.measure_sleep_time} s.')
        time.sleep(self.settings.measure_sleep_time)
        end_image = camera.grab_next_to_start()[0]
        end_time = time.time()
        aperture_mask = make_binary_image(start_image.data)
        sum_, center_y, center_x, a, b, angle = calculate_image_moments(aperture_mask)
        patch_size = max(min(0.8*b, self.settings.max_patch_radius), self.settings.min_patch_radius)
        patch_slice_tuple = (slice(int(round(center_y - patch_size)), int(round(center_y + patch_size))),
                             slice(int(round(center_x - patch_size)), int(round(center_x + patch_size))))
        cropped_start_image = start_image.data[patch_slice_tuple]
        cropped_end_image = end_image.data[patch_slice_tuple]
        ccorr_max, ccorr_max_location = xd.register_template(cropped_start_image, cropped_end_image)
        # ccorr_image = xd.crosscorrelate(cropped_start_image, cropped_end_image).data
        # ccorr_max_location = numpy.unravel_index(numpy.argmax(ccorr_image), ccorr_image.shape)
        # ccorr_max = ccorr_image[ccorr_max_location]
        if  ccorr_max < self.settings.ccorr_threshold:
            self.status_updated_event.fire(f'Poor correlation ({ccorr_max:.2f} < {self.settings.ccorr_threshold}).')
            return
        drift_vector = numpy.array(ccorr_max_location) * start_image.dimensional_calibrations[0].scale * defocus / (end_time - start_time)
        self.status_updated_event.fire(f'Measured drift (x, y): ({drift_vector[1]:.3g}, {drift_vector[0]:.3g}) m/s.')
        if not self.enabled:
            self.set_vector(DRIFT_VECTOR_CONTROL, (0.0, 0.0))
        self.inform_vector(DRIFT_RATE_CONTROL, drift_vector[::-1])

    def measure_drift_scan(self) -> None:
        scan = self.__stem_controller.scan_controller
        start_image = scan.grab_next_to_start()[0]
        start_time = time.time()
        self.status_updated_event.fire(f'Waiting for {self.settings.measure_sleep_time} s.')
        time.sleep(self.settings.measure_sleep_time)
        end_image = scan.grab_next_to_start()[0]
        end_time = time.time()
        ccorr_max, ccorr_max_location = xd.register_template(start_image, end_image)
        if  ccorr_max < self.settings.ccorr_threshold:
            self.status_updated_event.fire(f'Poor correlation ({ccorr_max:.2f} < {self.settings.ccorr_threshold}).')
            return
        drift_vector = numpy.array(ccorr_max_location) * start_image.dimensional_calibrations[0].scale * 1e-9/ (end_time - start_time)
        self.status_updated_event.fire(f'Measured drift (u, v): ({drift_vector[1]:.3g}, {drift_vector[0]:.3g}) m/s.')
        if not self.enabled:
            self.set_vector(DRIFT_VECTOR_CONTROL, (0.0, 0.0))
        self.inform_vector(DRIFT_RATE_CONTROL, drift_vector[::-1])

    def measure_drift(self) -> None:
        if self.axis == ('x', 'y'):
            self.measure_drift_camera()
        elif self.axis == ('u', 'v'):
            self.measure_drift_scan()
        else:
            raise ValueError(f'Axis {self.axis} is not supported for drift measurement.')


def run() -> None:
    class DriftCorrectionPanel(Panel.Panel):
        def __init__(self, document_controller: DocumentController.DocumentController, panel_id: str, properties: typing.Mapping[str, typing.Any]):
            super().__init__(document_controller, panel_id, 'drift-correction-panel')
            stem_controller = Registry.get_component('stem_controller')
            ui_handler = DriftCorrectionUI().get_ui_handler(stem_controller, PlugInManager.APIBroker(), document_controller.event_loop)
            self.widget = Declarative.DeclarativeWidget(document_controller.ui, document_controller.event_loop, ui_handler)

    Workspace.WorkspaceManager().register_panel(DriftCorrectionPanel, 'nion-drift-correction-panel', _('Drift Correction'), ['left', 'right'], 'right', {'panel_type': 'drift-correction-panel'})


def _find_correct_port() -> typing.Optional[int]:
    for port in [41532, 8090]:
        result, message = query([], timeout=1.0, port=port)
        if not message:
            return port
    return None

def get_url(*args: typing.Any, port: typing.Optional[int]=None) -> str:
    if port is None:
        port = 41532
    url = f'http://localhost:{port}/AS2'
    for arg in args:
        url += '/' + arg
    return url

def query(path_components: typing.Iterable[str], value: typing.Optional[typing.Mapping[str, typing.Any]]=None, timeout: float=5.0, port: typing.Optional[int]=None) -> typing.Tuple[typing.Mapping[str, typing.Any], str]:
        url = get_url(*path_components)
        data = None
        method = 'GET'
        headers = dict()
        result = dict()
        message = ''
        if value is not None:
            data_dict: typing.Dict[str, typing.Any] = dict()
            data_dict.update(value)
            data = json.dumps(data_dict).encode('utf-8')
            method = 'PUT'
            headers['Content-Type'] = 'application/json'
        req = urllib.request.Request(url=url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                bytes_ = response.read()
                if bytes_:
                    result = json.loads(bytes_.decode('utf-8'))
        except urllib.error.HTTPError as e:
            message += 'Query failed with error {}'.format(str(e))
        else:
            if (method == 'GET' and response.status != 200) or (method == 'PUT' and response.status != 204):
                message += 'Query failed with error code {:.0f}. Reason: {}'.format(response.status, response.reason)
        return result, message
