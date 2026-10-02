import typing

import asyncio
from dataclasses import dataclass
import gettext
import math
import numpy
import numpy.typing
import pathlib
import time

from nion.instrumentation import camera_base
from nion.instrumentation import stem_controller as stem_controller_module
from nion.swift import DocumentController
from nion.swift import Panel
from nion.swift import Workspace
from nion.swift.model import ImportExportManager
from nion.swift.model import PlugInManager
from nion.typeshed import API_1_0
from nion.ui import Declarative, UserInterface
from nion.utils import Converter
from nion.utils import Geometry
from nion.utils import Model
from nion.utils import Registry

_ = gettext.gettext
JSONType = stem_controller_module.JSONType
max_size = 32000  # this is the maximum size of the final image in pixels that can be pushed to the sample navigation window. Placeholder value at the moment because something weird is happening with AS2 where the max possible size is decreasing


@dataclass
class DimensionsResult:
    pixel_size: float
    frame_size: tuple[int, int]
    frame_width: float
    master_sub_area: tuple[tuple[int, int], tuple[int, int]]
    master_sub_area_size: tuple[int, int]
    sub_area_shift: float
    sub_area: tuple[tuple[int, int], tuple[int, int]]


@dataclass
class AcquisitionTimingResult:
    master_data: numpy.typing.NDArray[numpy.float64]
    total_images: int
    time_total: float
    total_image_size: tuple[int, int]


@dataclass
class AcquisitionFullResult:
    master_data: numpy.typing.NDArray[numpy.float64]
    sub_area: tuple[tuple[int, int], tuple[int, int]]
    sub_area_shift: float
    pixel_size: float
    total_image_height: float
    sx: float
    sy: float


class OverviewScanPanelUI:
    panel_type = "overview-scan-panel"

    @staticmethod
    def get_ui_handler(
            api_broker: PlugInManager.APIBroker,
            event_loop: typing.Optional[asyncio.AbstractEventLoop] = None,
            **kwargs: typing.Any,
    ) -> Declarative.HandlerLike:
        api = api_broker.get_api("~1.0")
        document_controller = typing.cast(DocumentController.DocumentController, kwargs.get("document_controller"))
        return OverviewSamplePanelHandler(api, event_loop, document_controller)


class OverviewSamplePanelHandler(Declarative.Handler):

    def __init__(self,
                 api: "API_1_0.API",
                 event_loop: typing.Optional[asyncio.AbstractEventLoop],
                 document_controller: DocumentController.DocumentController) -> None:
        super().__init__()
        self._api = api
        self._event_loop = event_loop or asyncio.get_event_loop()
        self.stem_controller = typing.cast(stem_controller_module.STEMController, Registry.get_component('stem_controller'))
        self.camera = typing.cast(camera_base.CameraHardwareSource, self.stem_controller.ronchigram_camera)
        self.integer_to_string_converter = Converter.IntegerToStringConverter()
        self.float_to_string_converter = Converter.FloatToStringConverter(pass_none=True)
        self._width_value_m: float = 3e-5
        self._height_value_m: float = 3e-5
        self._defocus_m: float = -5e-5 # defocus is in metres here
        self._binning: int = 1
        self.output_widget: UserInterface.TextEditWidget | None = None
        self.progress_value: int = 0
        self.progress_max: int = 100
        self.progress_min: int = 0
        self.progress_text: str = "Progress:\nIdle"
        self._acquisition_task: asyncio.Task[None] | None = None
        self._cancel_requested: bool = False
        self._is_running: bool = False
        self.cancel_enabled = Model.PropertyModel(False)
        self.scan_buttons_enabled = Model.PropertyModel(True)
        self.ui_view = self._build_ui()

    @property
    def width_value_m(self) -> float:
        return self._width_value_m

    @property
    def width_value_um(self) -> int:
        return int(self._width_value_m * 1e6)

    @width_value_um.setter
    def width_value_um(self, value: int) -> None:
        if value is None or value < 1:
            self._append_output_threadsafe("Width must be a positive integer. Returning to default value.\n")
            return
        width_um = value * 1e-6
        if width_um != self._width_value_m:
            self._width_value_m = width_um
            self.notify_property_changed("width_value_um")

    @property
    def height_value_m(self) -> float:
        return self._height_value_m

    @property
    def height_value_um(self) -> int:
        return int(self._height_value_m * 1e6)

    @height_value_um.setter
    def height_value_um(self, value: int) -> None:
        if value is None or value < 1:
            self._append_output_threadsafe("Height must be a positive integer. Returning to default value.\n")
            return
        height_um = value * 1e-6
        if height_um != self._height_value_m:
            self._height_value_m = height_um
            self.notify_property_changed("height_value_um")


    @property
    def defocus_m(self) -> float:  # defocus is in nm here
        return self._defocus_m

    @property
    def defocus_nm(self) -> float:  # defocus is in nm here
        return int(self._defocus_m * 1e9)

    @defocus_nm.setter
    def defocus_nm(self, value: float | None) -> None:
        if value is None or abs(value) > 500000:
            self._append_output_threadsafe(f"Defocus must be between -500000 and 500000 nm. Returning to default value.\n")
            return
        defocus_nm = value * 1e-9
        if defocus_nm != self._defocus_m:
            self._defocus_m = defocus_nm
            self.notify_property_changed("defocus_nm")

    @property
    def binning(self) -> int:
        return self._binning

    @binning.setter
    def binning(self, value: int) -> None:
        if value is None or value < 1:
            self._append_output_threadsafe("Binning must be a positive integer. Returning to default value.\n")
            return
        if value != self._binning:
            self._binning = value
            self.notify_property_changed("binning")

    def _set_progress(self, value: int, maximum: int, text: str) -> None:
        """
        Set the progress value, maximum, and text for the progress bar.
        """
        self.progress_value = value
        self.progress_max = max(1, int(maximum))
        self.progress_min = 0
        self.progress_text = text
        self.notify_property_changed("progress_value")
        self.notify_property_changed("progress_text")

    def _set_progress_threadsafe(self, value: int, maximum: int, text: str) -> None:
        """
        Thread-safe method to set the progress value, maximum, and text for the progress bar, so it can be updated during acquisition.
        """
        self._event_loop.call_soon_threadsafe(self._set_progress, value, maximum, text)

    @staticmethod
    def _build_ui() -> Declarative.UIDescription:
        """
        Construct the UI for the Overview Scan panel, including labels, buttons, input fields, and a progress bar.
        """
        u = Declarative.DeclarativeUI()
        time_button = u.create_push_button(text="Estimate scan size and duration", on_clicked="handle_estimate_time_clicked", enabled="@binding(scan_buttons_enabled.value)")
        acquisition_button = u.create_push_button(text="Scan", on_clicked="handle_perform_acquisition_clicked", enabled="@binding(scan_buttons_enabled.value)")
        max_button = u.create_push_button(text="Calculate maximum scan", on_clicked="handle_max_clicked", enabled="@binding(scan_buttons_enabled.value)")
        properties_label = u.create_label(text="Desired properties of image:")
        width_label = u.create_label(text="Width (μm):", width=80)
        width_field = u.create_line_edit(text="@binding(width_value_um, converter=integer_to_string_converter)", width=50)
        height_label = u.create_label(text="Height (μm):", width=80)
        height_field = u.create_line_edit(text="@binding(height_value_um, converter=integer_to_string_converter)", width=50)
        defocus_label = u.create_label(text="Defocus (nm):", width=80)
        defocus_field = u.create_line_edit(text="@binding(defocus_nm, converter=float_to_string_converter)", width=50)
        binning_label = u.create_label(text="Binning:")
        binning_field = u.create_line_edit(text="@binding(binning, converter=integer_to_string_converter)", width=50)
        output_label = u.create_label(text="Output:")
        output_box = u.create_text_edit(name="output_widget", editable=False, height=200)
        progress_label = u.create_label(text="@binding(progress_text)")
        progress_bar = u.create_progress_bar(value="@binding(progress_value)", minimum=0, maximum=100, width=300)
        cancel_button = u.create_push_button(text="Cancel", on_clicked="handle_cancel_acquisition_clicked", enabled="@binding(cancel_enabled.value)")
        clear_button = u.create_push_button(text="Clear minimap", on_clicked="handle_clear_minimap_clicked", enabled="@binding(scan_buttons_enabled.value)")

        overview_scan_ui = u.create_column(
            properties_label,
            u.create_row(
                u.create_row(width_label, width_field),
                u.create_row(height_label, height_field),
            ),
            u.create_row(
                u.create_row(defocus_label, defocus_field),
                u.create_row(binning_label, binning_field)
            ),
            u.create_row(max_button, time_button),
            acquisition_button,
            u.create_spacing(8),
            progress_label,
            progress_bar,
            u.create_spacing(8),
            u.create_row(cancel_button),
            u.create_spacing(4),
            u.create_row(clear_button),
            output_label,
            output_box,
            u.create_stretch(),
            margin=6,
            spacing=4
        )

        return typing.cast(typing.Mapping[str, typing.Any], overview_scan_ui)

    def _append_output(self, message: str) -> None:
        """
        Add text to the output window.
        """
        if self.output_widget is not None:
            self.output_widget.move_cursor_position("end")
            self.output_widget.append_text(message)

    def _append_output_threadsafe(self, message: str) -> None:
        """
        Update output window contemporaneously with acquisition.
        """
        self._event_loop.call_soon_threadsafe(self._append_output, message)

    def _get_axis_description(self, axis_name: str) -> stem_controller_module.AxisDescription:
        for axis_description in self.stem_controller.axis_descriptions:
            if axis_description.axis_id == axis_name:
                return axis_description
            if axis_description.display_name == axis_name:
                return axis_description
            if getattr(axis_description, "searchable_name", None) == axis_name:
                return axis_description
        raise ValueError(f"Axis '{axis_name}' not found.")

    @staticmethod
    def find_properties(stem_controller: stem_controller_module.STEMController,
                        camera: camera_base.CameraHardwareSource,
                        defocus_m: float,
                        tv_pixel_angle_rad: float,
                        binning: float = 1.0) -> DimensionsResult:
        """
        Calculate the relevant properties of each frame based on the provided defocus and TV pixel angle.

        Args:
        - stem_controller: the instrument used to control the STEM microscope.
        - camera: the Ronchigram camera used to capture images.
        - defocus_m: the desired defocus value in meters.
        - tv_pixel_angle_rad: the TV pixel angle in radians.
        - binning: the binning factor for the camera, which reduces the resolution of the captured images by combining adjacent pixels.

        Returns:
        - pixel_size_m: real-world size of each pixel in the image in meters.
        - frame_size_px: dimensions of each frame in pixels
        - frame_width_m: real-world width of the image in meters.
        - master_sub_area: the full-size crop taken from the frame
        - master_sub_area_size: the size of that crop in pixels
        - sub_area_shift_m: the real-world distance in meters that the stage needs to move to capture the next frame in the snake pattern.
        - sub_area: the binned crop taken from the frame, which is used to construct the final image
        """
        stem_controller.set_control_output("C10", defocus_m)  # set the defocus to the desired value

        # Get pixel size, image size, and image width based on the defocus and TV pixel angle
        pixel_size_m = abs(defocus_m) * math.tan(tv_pixel_angle_rad)
        frame_size_px = camera.get_expected_dimensions(camera.get_current_frame_parameters())
        frame_width_m = abs(defocus_m) * math.sin(tv_pixel_angle_rad * frame_size_px[0])

        # Calculate the area of the image and the master sub-area based on the image size and reduce factor
        master_sub_area_size = frame_size_px[0], frame_size_px[1]
        master_sub_area = (frame_size_px[0] // 2 - master_sub_area_size[0] // 2,
                           frame_size_px[1] // 2 - master_sub_area_size[1] // 2), master_sub_area_size
        binning = max(1, int(binning))

        sub_area_shift_m = frame_width_m * (master_sub_area[1][0] / frame_size_px[0])
        sub_area_height = len(range(master_sub_area[0][0], master_sub_area[0][0] + master_sub_area[1][0], binning))
        sub_area_width = len(range(master_sub_area[0][1], master_sub_area[0][1] + master_sub_area[1][1], binning))

        sub_area = (
            (master_sub_area[0][0] // binning, master_sub_area[0][1] // binning),
            (sub_area_height, sub_area_width),
        )

        return DimensionsResult(
    pixel_size=pixel_size_m,
    frame_size=frame_size_px,
    frame_width=frame_width_m,
    master_sub_area=master_sub_area,
    master_sub_area_size=master_sub_area_size,
    sub_area_shift=sub_area_shift_m,
    sub_area=sub_area,
)

    def acquisition(self,
                    stem_controller: stem_controller_module.STEMController,
                    camera: camera_base.CameraHardwareSource,
                    defocus_m: float,
                    target_width: float, target_height: float, timer: bool = False,
                    binning: float = 1.0) -> AcquisitionTimingResult | AcquisitionFullResult | tuple[int, float] | None:
        """
        Move across the sample in a snake pattern, acquiring images at each position, and return the resulting data and relevant parameters.
        If timer is True, return an estimate of how long the full acquisition will take.

        Args:
        - stem_controller: the instrument used to control the STEM microscope.
        - camera: the Ronchigram camera used to capture images.
        - defocus_m: the desired defocus value in meters.
        - target_width_m: the desired width of the final image in meters.
        - target_height_m: the desired height of the final image in meters.
        - binning: the binning factor for the camera, which reduces the resolution of the captured images by combining adjacent pixels.
        - timer: if True, the function will only acquire two frames to estimate the time required for the full acquisition.
               if False, the function will perform the full acquisition.

        Returns:
        if timer is True:
            - master_data: the acquired data
            - total_images: the total number of images the acquisition needs
            - time_total: the total time for the acquisition of two frames
        if timer is False:
            - master_data: the acquired data
            - sub_area: the binned crop taken from the frame, which is used to construct the final image
            - sub_area_shift: the real-world distance in meters that the stage needs to move to capture the next frame in the snake pattern.
            - pixel_size: real-world size of each pixel in the image in meters.
            - total_image_height: the real_world height of the final data item in metres
            - sx, sy: the original stage coordinates.
        """
        counter = 0
        self._cancel_requested = False
        self._is_running = True
        self.cancel_enabled.value = True
        self.scan_buttons_enabled.value = False

        success, pixel_angle_rad = stem_controller.TryGetVal("TVPixelAngle")  # if success is False, the plugin is likely being run on uSim

        if success:  #even if success is True, it could still be on usim- this would give an empty or singular matrix so can guard against non-uSim controls being used
            #  this branch will run where the plugin is used on an actual microscope
            shift_x_control_name = "SShft.sx"
            shift_y_control_name = "SShft.sy"

        else:
            #  this allows the plugin to run on uSim
            shift_x_control_name = "stage_position_m.x"
            shift_y_control_name = "stage_position_m.y"

            frame = camera.grab_next_to_start()[0]
            assert frame is not None
            pixel_angle_rad = float(frame.dimensional_calibrations[0].scale)

        # grab stage original location and original defocus
        sx = stem_controller.get_control_output(shift_x_control_name)
        sy = stem_controller.get_control_output(shift_y_control_name)
        df_original = stem_controller.get_control_output("C10")

        assert pixel_angle_rad is not None
        stem_controller.set_control_output("C10", defocus_m)

        properties = self.find_properties(stem_controller, camera, defocus_m, pixel_angle_rad, binning)
        pixel_size_m = properties.pixel_size
        frame_width_m = properties.frame_width
        master_sub_area = properties.master_sub_area
        sub_area_shift_m = properties.sub_area_shift
        sub_area = properties.sub_area

        # calculate the number of frames to cover the target area
        frames_needed_width = math.ceil(target_width / sub_area_shift_m)
        frames_needed_height = math.ceil(target_height / sub_area_shift_m)

        t1 = time.time()
        time_total = 0.0

        if timer:
            dimensions = (2, 1)  # for timing purposes, only need to acquire 2 frames and average the time to take them both
        else:
            dimensions = (frames_needed_width, frames_needed_height)

        master_data = numpy.empty((sub_area[1][0] * dimensions[0], sub_area[1][1] * dimensions[1]))
        total_image_height_um = frames_needed_height * frame_width_m  # calculate the height of the image in um
        total_images = frames_needed_width * frames_needed_height  # calculate the total number of frames required for the image
        total_image_size_px = (sub_area[1][0] * frames_needed_width, sub_area[1][1] * frames_needed_height)  # calculate the total size of the image in pixels

        if not timer:  # if performing the full acquisition instead of just estimating the time, update the progress bar and output window
            self._append_output_threadsafe(f"Stage starting position: {(sx * 1e6):.3f}, {(sy * 1e6):.3f} μm")
            self._append_output_threadsafe(f"Pixel size: {(pixel_size_m * 1e9):.3f} nm")
            self._append_output_threadsafe(f"Frame width: {(frame_width_m * 1e6):.3f} μm")
            self._append_output_threadsafe(f"Master size: {master_data.shape}\n")

            self._set_progress_threadsafe(0, total_images, "Progress:\nStarting acquisition...")
        try:
            for row in range(dimensions[0]):
                #  cancel mechanism
                if self._cancel_requested:
                    self._append_output_threadsafe("Acquisition Cancelled.")
                    self.cancel_enabled.value = False
                    self.scan_buttons_enabled.value = True
                    return None if not timer else (0, 0.0)

                # acquisition algorithm in a snake pattern
                col_iter = range(dimensions[1]) if (row % 2 == 0) else range(dimensions[1] - 1, -1, -1)
                for column in col_iter:
                    if self._cancel_requested:
                        self._append_output_threadsafe("Acquisition Cancelled.")
                        self.cancel_enabled.value = False
                        self.scan_buttons_enabled.value = True
                        return None if not timer else (0, 0.0)

                    delta_x = - sub_area_shift_m * (column - dimensions[1] // 2)
                    delta_y = - sub_area_shift_m * (row - dimensions[0] // 2)

                    stage_axis = self._get_axis_description("StageAxis")
                    camera_axis = self._get_axis_description("TV")

                    delta_fast = stem_controller.axis_transform_point(Geometry.FloatPoint(y=delta_y, x=delta_x),from_axis=stage_axis, to_axis=camera_axis)

                    if delta_fast:
                        delta_x = float(delta_fast[0])
                        delta_y = float(delta_fast[1])

                    counter += 1
                    attempts = 0

                    while attempts < 4:
                        if self._cancel_requested:
                            self._append_output_threadsafe("Acquisition Cancelled.")
                            self.cancel_enabled.value = False
                            self.scan_buttons_enabled.value = True
                            return None if not timer else (0, 0.0)

                        attempts += 1

                        try:  # try to move the stage to the desired position, if it times out then try again up to 4 times
                            tolerance_factor = 0.0001
                            stem_controller.set_control_output(shift_x_control_name, sx - delta_x, {"confirm": True, "confirm_tolerance_factor": tolerance_factor})
                            stem_controller.set_control_output(shift_y_control_name, sy - delta_y, {"confirm": True, "confirm_tolerance_factor": tolerance_factor})
                        except TimeoutError:
                            self._append_output_threadsafe(f"Timeout row= {row} column= {column}")
                            continue
                        break

                    # adding the new frame to the data item
                    supradata = camera.grab_next_to_start()[0]
                    assert supradata is not None

                    data = supradata.data[master_sub_area[0][0]:master_sub_area[0][0] + master_sub_area[1][0]:binning, master_sub_area[0][1]:master_sub_area[0][1] + master_sub_area[1][1]:binning]
                    slice_row = row
                    slice_column = column
                    slice0 = slice(slice_row * sub_area[1][0], (slice_row + 1) * sub_area[1][0])
                    slice1 = slice(slice_column * sub_area[1][1], (slice_column + 1) * sub_area[1][1])
                    master_data[slice0, slice1] = data

                    if not timer:  # if performing the actual acquisition then update the progress bar and output window
                        pct = int(100 * counter / total_images)
                        self._set_progress_threadsafe(pct, total_images, f"Progress:\nAcquiring frame {counter} of {total_images}")

                t2 = time.time()
                time_total = t2 - t1

        finally:
            # restore stage to original location
            stem_controller.set_control_output(shift_x_control_name, sx)
            stem_controller.set_control_output(shift_y_control_name, sy)

            stem_controller.set_control_output("C10", df_original)  # restore defocus to original value
            self._set_progress_threadsafe(0, 100, "Progress:\nIdle")  # reset progress bar to idle state

        if timer:
            return AcquisitionTimingResult(
                master_data=master_data,
                total_images=total_images,
                time_total=time_total,
                total_image_size=total_image_size_px,
            )
        else:
            return AcquisitionFullResult(
                master_data=master_data,
                sub_area=sub_area,
                sub_area_shift=sub_area_shift_m,
                pixel_size=pixel_size_m,
                total_image_height=total_image_height_um,
                sx=sx,
                sy=sy,
            )

    def handle_cancel_acquisition_clicked(self, widget: Declarative.UIWidget) -> None:
        """
        Cancel button: off when the acquisition is not running, on when it is.
        """
        if self._is_running:
            self._cancel_requested = True
            self._set_progress_threadsafe(self.progress_value, 100, "Cancel requested...")


    def handle_estimate_time_clicked(self, widget: Declarative.UIWidget) -> None:
        """
        Estimates the time an acquisition will take by averaging the time it takes to capture two frames and multiplying by the total number of frames required for the acquisition.
        """
        width_m = self.width_value_m
        height_m = self.height_value_m
        defocus_m = self.defocus_m
        binning = self._binning

        stem_controller = self.stem_controller
        camera = self.camera

        result = self.acquisition(stem_controller, camera, defocus_m, width_m, height_m, timer=True, binning=binning)

        total_images = result.total_images
        t_total = result.time_total
        image_size_px = result.total_image_size
        time_taken = t_total * total_images / 2  # average time to move the stage
        self._append_output(f"This acquisition will take approximately {(time_taken // 3600):.0f}h {((time_taken % 3600) / 60):.0f}min {(time_taken % 60):.0f}s")

        if any(dimension > max_size for dimension in image_size_px):
            self._append_output("The final data item is too large to be used in the sample navigation window. Consider increasing the binning or reducing the size of the acquisition.\n")
            self.cancel_enabled.value = False
            self.scan_buttons_enabled.value = True
            self._set_progress_threadsafe(0, 100, "Progress:\nIdle")

            return
        else:
            self._append_output(f"The final data item will have dimensions {image_size_px[0]} x {image_size_px[1]} pixels.\n")
            self.cancel_enabled.value = False
            self.scan_buttons_enabled.value = True
            self._set_progress_threadsafe(0, 100, "Progress:\nIdle")

            return

    async def _run_acquisition_async(self,
                                     stem_controller: stem_controller_module.STEMController,
                                     camera: camera_base.CameraHardwareSource,
                                     defocus_m: float,
                                     target_width: float, target_height: float,
                                     binning: int) -> None:
        """
        Performs acquisition asynchronously to avoid blocking the UI thread, then pushes results to the sample navigation map in AS2.
        Calculates dimensional calibrations for the final data item and creates a new data item in the library.
        Uses REST API calls to get and set the cartridge properties for the sample navigation map.

        Args:
        - stem_controller: the instrument used to control the STEM microscope.
        - camera: the Ronchigram camera used to capture images.
        - defocus_m: the desired defocus value in meters.
        - binning: the binning factor for the camera, which reduces the resolution of the captured images by combining adjacent pixels.
        """
        loop = self._event_loop

        self._append_output_threadsafe("Starting acquisition...\n")
        try:
            result = await loop.run_in_executor(None, self.acquisition, stem_controller, camera, defocus_m, target_width, target_height, False, binning)
            self._set_progress(0, 100, "Progress:\nIdle")

            master_data = result.master_data
            sub_area = result.sub_area
            sub_area_shift_m = result.sub_area_shift
            pixel_size_m = result.pixel_size
            total_image_height_m = result.total_image_height
            sx = result.sx
            sy = result.sy
        except Exception as e:
            self._append_output(f"Acquisition failed: {e!r}")
            self.cancel_enabled.value = False
            self.scan_buttons_enabled.value = True
            self._set_progress_threadsafe(0, 100, "Progress:\nIdle")
            return

        try:
            # dimensional calibrations for the final data item
            library = self._api.library
            y_scale_um = (sub_area_shift_m / sub_area[1][0]) * 1e6
            x_scale_um = (sub_area_shift_m / sub_area[1][1]) * 1e6
            dimensional_calibrations = [
                self._api.create_calibration(0.0, y_scale_um, "um"),
                self._api.create_calibration(0.0, x_scale_um, "um"),
            ]
            data_descriptor = self._api.create_data_descriptor(False, 0, 2)

            xdata = self._api.create_data_and_metadata(master_data, dimensional_calibrations=dimensional_calibrations, data_descriptor=data_descriptor)
            # create final data item
            self._append_output_threadsafe("Creating data item in library...\n")
            data_item = library.create_data_item_from_data_and_metadata(xdata, "Composite Survey")
            document_window = self._api.application.document_controllers[0]
            document_window.display_data_item(data_item)

            await asyncio.sleep(5) # allow time for the display to be created so the image exporter doesn't throw an assertion error- needs more the bigger the data item

            display = data_item.display
            display.display_type = "image"
            display_item = display._display_item
            data_path = pathlib.Path(r"C:\AS2\AS2User\Pictures\overview-scan.jpg")

            ImportExportManager.ImportExportManager().write_display_item(display_item, data_path)

            self._append_output_threadsafe("Acquisition complete.\n")

            self._append_output_threadsafe("Image properties:")
            self._append_output_threadsafe(f"Total image height: {(total_image_height_m * 1e3):.3f} mm")
            self._append_output_threadsafe(f"Original stage coordinates: {(sx * 1e6):.3f}, {(sy * 1e6):.3f} μm")
            self.cancel_enabled.value = False
            self.scan_buttons_enabled.value = True

        except Exception as e:
            self._append_output(f"Failed to publish result: {e!r}")
            self.cancel_enabled.value = False
            self.scan_buttons_enabled.value = True
            return

        # push the image, scale height and offsets to the sample navigation map
        try:
            self._append_output_threadsafe("Pushing image to minimap...")
            cartridge_result = stem_controller._get_rest_api("/exchange?property=CartridgeInStage")
            if cartridge_result.is_valid:
                cartridge_string = cartridge_result.value
                self._append_output_threadsafe(f"Cartridge in stage: {cartridge_string}")
                properties: JSONType = {"ImageScaleRad_m": total_image_height_m / 2, "ImageOffsetX_px": sx / pixel_size_m, "ImageOffsetY_px": sy / pixel_size_m, "ImageFile": str(data_path)}

                # Set the values on the cartridge

                property_result = stem_controller._put_rest_api(f"/exchange/cartridges/{cartridge_string}", content=properties)
                if not property_result.is_valid:
                    self._append_output_threadsafe(f"PUT failed: {property_result.exception}")
                    self.cancel_enabled.value = False
                    self.scan_buttons_enabled.value = True

            else:
                self._append_output_threadsafe(f"Failed to get CartridgeInStage: {cartridge_result.exception}")
                self.cancel_enabled.value = False
                self.scan_buttons_enabled.value = True
                return

        except Exception as e:
            self._append_output(f"Failed to update cartridge data: {e!r}")
            self.cancel_enabled.value = False
            self.scan_buttons_enabled.value = True
            return

    def handle_perform_acquisition_clicked(self, widget: Declarative.UIWidget) -> None:
        """
        Starts the acquisition process by validating input parameters.
        Initiates the asynchronous acquisition task.
        """
        width_m = self.width_value_m
        height_m = self.height_value_m
        defocus_m = self.defocus_m
        binning = self._binning

        if self._acquisition_task and not self._acquisition_task.done():
            self._append_output("Acquisition already running.")
            return

        self._acquisition_task = self._event_loop.create_task(
            self._run_acquisition_async(self.stem_controller, self.camera, defocus_m, width_m, height_m, binning)
        )
        self.cancel_enabled.value = False
        self.scan_buttons_enabled.value = True
        self._set_progress_threadsafe(0, 100, "Progress:\nIdle")

    def handle_max_clicked(self, widget: Declarative.UIWidget) -> None:
        """
        Calculates the maximum scan size at the specified defocus/binning for the image to be pushed to the sample navigation map.
        Estimates the time it will take.
        """
        defocus_m = self.defocus_m
        binning = self._binning

        stem_controller = self.stem_controller
        camera = self.camera

        # calculating the maximum scan size at the specified defocus/binning for the image to be pushed to the sample navigation map
        success, tv_pixel_angle_rad = stem_controller.TryGetVal("TVPixelAngle")

        if not success:
            frame = camera.grab_next_to_start()[0]
            assert frame is not None
            tv_pixel_angle_rad = float(frame.dimensional_calibrations[0].scale)

        assert tv_pixel_angle_rad is not None

        properties = self.find_properties(stem_controller, camera, defocus_m, tv_pixel_angle_rad, binning)
        sub_area_shift_m = properties.sub_area_shift
        sub_area = properties.sub_area

        dimension_y = max_size // sub_area[1][0]
        dimension_x = max_size // sub_area[1][1]

        # putting the calculated maximum scan size into the width and height fields in the UI
        self.width_value_um = int(dimension_x * sub_area_shift_m * 1e6) # convert to micrometers
        self.height_value_um = int(dimension_y * sub_area_shift_m * 1e6)

    def handle_clear_minimap_clicked(self, widget: Declarative.UIWidget) -> None:
        """
        Clears the image, scale height and offsets from the sample navigation map.
        """
        stem_controller = self.stem_controller
        try:
            cartridge_result = stem_controller._get_rest_api("/exchange?property=CartridgeInStage")
            if cartridge_result.is_valid:
                cartridge_string = cartridge_result.value
                properties: JSONType = {"ImageScaleRad_m": 0.0, "ImageOffsetX_px": 0.0, "ImageOffsetY_px": 0.0, "ImageFile": ""}
                stem_controller._put_rest_api(f"/exchange/cartridges/{cartridge_string}", content=properties)
                self._append_output_threadsafe("Minimap cleared.")
            else:
                self._append_output_threadsafe(f"Failed to get CartridgeInStage: {cartridge_result.exception}")
        except Exception as e:
            self._append_output(f"Failed to clear minimap data: {e!r}")


class OverviewScanPanel(Panel.Panel):

    def __init__(self,
                 document_controller: "DocumentController.DocumentController",
                 panel_id: str,
                 properties: typing.Dict[str, typing.Any]) -> None:
        super().__init__(document_controller, panel_id, "overview-scan-panel")
        for component in Registry.get_components_by_type("overview-scan-panel"):
            if getattr(component, "panel_type", None) == "overview-scan-panel":
                ui_handler = component.get_ui_handler(
                    api_broker=PlugInManager.APIBroker(),
                    event_loop=document_controller.event_loop,
                    document_controller=document_controller,
                )
                self.widget = Declarative.DeclarativeWidget(
                    document_controller.ui,
                    document_controller.event_loop,
                    ui_handler,
                )
                break


class OverviewScanPanelExtension:

    extension_id = "overview-scan.panel"

    def __init__(self, api_broker: typing.Any) -> None:
        Registry.register_component(OverviewScanPanelUI(), {"overview-scan-panel"})
        Workspace.WorkspaceManager().register_panel(
            OverviewScanPanel,
            "overview-scan-main-panel",
            _("Overview Scan"),
            ["left", "right"],
            "right",
            {"panel_type": "overview-scan-panel"},
        )

    def close(self) -> None:
        pass