import typing

import asyncio
import gettext
import math
import numpy
import numpy.typing as npt
from pathlib import Path
from PIL import Image
import time

from nion.instrumentation import camera_base
from nion.instrumentation import stem_controller as stem_controller_module
from nion.swift import DocumentController
from nion.swift import Panel
from nion.swift import Workspace
from nion.swift.model import PlugInManager
from nion.typeshed import API_1_0
from nion.ui import Declarative
from nion.utils import Model
from nion.utils import Registry

_ = gettext.gettext
JSONDict = dict[str, typing.Any]
max_size = 32000  # this is the maximum size of the final image in pixels that can be pushed to the sample navigation window. Placeholder value at the moment because something weird is happening with AS2 where the max possible size is decreasing


class OverviewScanPanelUI:
    panel_type = "overview-scan-panel"

    @staticmethod
    def get_ui_handler(
            api_broker: PlugInManager.APIBroker,
            event_loop: typing.Optional[asyncio.AbstractEventLoop] = None,
            **kwargs: typing.Any,
    ) -> Declarative.HandlerLike:
        api = api_broker.get_api("~1.0")
        document_controller = kwargs.get("document_controller")
        return OverviewSamplePanelHandler(api, event_loop, document_controller)


class OverviewSamplePanelHandler(Declarative.Handler):

    def __init__(self,
                 api: "API_1_0.API",
                 event_loop: typing.Optional[asyncio.AbstractEventLoop],
                 document_controller: typing.Any) -> None:
        super().__init__()
        self._api = api
        self._event_loop = event_loop or asyncio.get_event_loop()
        self.stem_controller = typing.cast(stem_controller_module.STEMController, Registry.get_component('stem_controller'))
        self.camera = typing.cast(camera_base.CameraHardwareSource, self.stem_controller.ronchigram_camera)
        self._document_controller = document_controller
        self.width_value: str = "30"
        self.height_value: str = "30"
        self.defocus: str = "-50000"
        self.binning: str = "1"
        self.output_text: str = ""
        self.progress_value: int = 0
        self.progress_max: int = 100
        self.progress_min: int = 0
        self.progress_text: str = "Progress:\nIdle"
        self._acq_task: typing.Optional[asyncio.Task[None]] = None
        self._cancel_requested: bool = False
        self._is_running: bool = False
        self.cancel_enabled = Model.PropertyModel(False)
        self.scan_buttons_enabled = Model.PropertyModel(True)
        self.ui_view = self._build_ui()

    def _set_progress(self, value: int, maximum: int, text: str) -> None:
        """
        Set the progress value, maximum, and text for the progress bar.
        """
        self.progress_value = value
        self.progress_max = max(1, int(maximum))
        self.progress_min = 0
        self.progress_text = text
        self.property_changed_event.fire("progress_value")
        self.property_changed_event.fire("progress_text")

    def _set_progress_threadsafe(self, value: int, maximum: int, text: str) -> None:
        """
        Thread-safe method to set the progress value, maximum, and text for the progress bar, so it can be updated during acquisition.
        """
        self._event_loop.call_soon_threadsafe(self._set_progress, value, maximum, text)

    @staticmethod
    def _build_ui() -> typing.Mapping[str, typing.Any]:
        """
        Construct the UI for the Overview Scan panel, including labels, buttons, input fields, and a progress bar.
        """
        u = Declarative.DeclarativeUI()
        title = u.create_label(text="Overview Scan", font="bold")
        time_button = u.create_push_button(text="Estimate scan size and duration", on_clicked="handle_estimate_time_clicked", enabled="@binding(scan_buttons_enabled.value)")
        acq_button = u.create_push_button(text="Scan", on_clicked="handle_perform_acquisition_clicked", enabled="@binding(scan_buttons_enabled.value)")
        max_button = u.create_push_button(text="Calculate maximum scan", on_clicked="handle_max_clicked", enabled="@binding(scan_buttons_enabled.value)")
        properties_label = u.create_label(text="Desired properties of image:")
        width_label = u.create_label(text="Width (um):", width=80)
        width_field = u.create_line_edit(text="@binding(width_value)", width=50, editable=True)
        height_label = u.create_label(text="Height (um):", width=80)
        height_field = u.create_line_edit(text="@binding(height_value)", width=50, editable=True)
        defocus_label = u.create_label(text="Defocus (nm):", width=80)
        defocus_field = u.create_line_edit(text="@binding(defocus)", width=50, editable=True)
        binning_label = u.create_label(text="Binning:")
        binning_field = u.create_line_edit(text="@binding(binning)", width=50, editable=True)
        output_label = u.create_label(text="Output:")
        output_box = u.create_text_edit(text="@binding(output_text)", editable=False, height=200)
        progress_label = u.create_label(text="@binding(progress_text)")
        progress_bar = u.create_progress_bar(value="@binding(progress_value)", minimum=0, maximum=100, width=600)
        cancel_button = u.create_push_button(text="Cancel", on_clicked="handle_cancel_acquisition_clicked", enabled="@binding(cancel_enabled.value)")
        clear_button = u.create_push_button(text="Clear minimap", on_clicked="handle_clear_minimap_clicked", enabled="@binding(scan_buttons_enabled.value)")

        overview_scan_ui = u.create_column(
            title,
            properties_label,
            u.create_row(
                u.create_row(u.create_column(width_label, spacing=0), u.create_column(width_field, spacing=0), spacing=2),
                u.create_row(u.create_column(height_label, spacing=0), u.create_column(height_field, spacing=0), spacing=2),
                u.create_row(u.create_column(defocus_label, spacing=0), u.create_column(defocus_field, spacing=0), spacing=2),
                u.create_row(u.create_column(binning_label, spacing=0), u.create_column(binning_field, spacing=0), spacing=8),
            ),
            u.create_row(max_button, time_button, acq_button, spacing=4),
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
        self.output_text += f"{message}\n"
        self.property_changed_event.fire("output_text")

    def _append_output_threadsafe(self, message: str) -> None:
        """
        Update output window contemporaneously with acquisition.
        """
        self._event_loop.call_soon_threadsafe(self._append_output, message)

    def find_matrix(self, ds: float = 16e-6) -> numpy.ndarray:
        """
        Calculate the transformation matrix from stage coordinates to camera coordinates by moving the stage in small increments and measuring the resulting changes in camera coordinates.
        This is done because moving along the stage axis is much faster than moving along the camera axis as it requires fewer moves.

        Args:
        - ds: the step size by which the stage is moved in the x and y directions to measure the resulting changes in camera coordinates.

        Returns:
        - matrix: a 2x2 numpy array representing the transformation matrix from stage coordinates to camera coordinates.
        """
        stem_controller = self.stem_controller

        # Get original stage position in both stage and camera coordinates
        sx0 = stem_controller.get_control_output("SShft.sx")
        sy0 = stem_controller.get_control_output("SShft.sy")
        x0 = stem_controller.get_control_output("SShft.x")
        y0 = stem_controller.get_control_output("SShft.y")

        #  Move a small amount in x direction in the stage axis and then measure the change in x and y in the camera axis
        stem_controller.set_control_output("SShft.sx", sx0 + ds)
        x1 = stem_controller.get_control_output("SShft.x")
        y1 = stem_controller.get_control_output("SShft.y")

        dx_from_sx = x1 - x0
        dy_from_sx = y1 - y0

        # Put the stage back to its original position
        stem_controller.set_control_output("SShft.sx", sx0)
        stem_controller.set_control_output("SShft.sy", sy0)
        stem_controller.set_control_output("SShft.x", x0)
        stem_controller.set_control_output("SShft.y", y0)

        # Move a small amount in y direction in the stage axis and then measure the change in x and y in the camera axis
        stem_controller.set_control_output("SShft.sy", sy0 + ds)
        x2 = stem_controller.get_control_output("SShft.x")
        y2 = stem_controller.get_control_output("SShft.y")

        dx_from_sy = x2 - x0
        dy_from_sy = y2 - y0

        # Put the stage back to its original position
        stem_controller.set_control_output("SShft.sy", sy0)
        stem_controller.set_control_output("SShft.sx", sx0)
        stem_controller.set_control_output("SShft.x", x0)
        stem_controller.set_control_output("SShft.y", y0)

        # Construct the transformation matrix from stage coordinates to camera coordinates
        matrix = numpy.array([
            [dx_from_sx / ds, dx_from_sy / ds],
            [dy_from_sx / ds, dy_from_sy / ds],
        ])

        return matrix

    @staticmethod
    def find_dimensions(stem_controller: stem_controller_module.STEMController,
                        camera: camera_base.CameraHardwareSource,
                        defocus: float,
                        tv_pixel_angle_rad: float,
                        binning: float = 1.0) -> tuple[float, tuple[int, int], float, tuple[tuple[int, int], tuple[int, int]], tuple[int, int], float, tuple[tuple[int, int], tuple[int, int]]]:
        """
        Calculate the relevant properties of each frame based on the provided defocus and TV pixel angle.

        Args:
        - stem_controller: the instrument used to control the STEM microscope.
        - camera: the Ronchigram camera used to capture images.
        - defocus: the desired defocus value in meters.
        - tv_pixel_angle_rad: the TV pixel angle in radians.
        - binning: the binning factor for the camera, which reduces the resolution of the captured images by combining adjacent pixels.

        Returns:
        - pixel_size: real-world size of each pixel in the image in meters.
        - frame_size: dimensions of each frame in pixels
        - frame_width: real-world width of the image in meters.
        - master_sub_area: the full-size crop taken from the frame
        - master_sub_area_size: the size of that crop in pixels
        - sub_area_shift: the real-world distance in meters that the stage needs to move to capture the next frame in the snake pattern.
        - sub_area: the binned crop taken from the frame, which is used to construct the final image
        """
        stem_controller.set_control_output("C10", defocus)  # set the defocus to the desired value

        # Get pixel size, image size, and image width based on the defocus and TV pixel angle
        pixel_size = abs(defocus) * math.tan(tv_pixel_angle_rad)
        frame_size = camera.get_expected_dimensions(camera.get_current_frame_parameters())
        frame_width = abs(defocus) * math.sin(tv_pixel_angle_rad * frame_size[0])

        # Calculate the area of the image and the master sub-area based on the image size and reduce factor
        master_sub_area_size = frame_size[0], frame_size[1]
        master_sub_area = (frame_size[0] // 2 - master_sub_area_size[0] // 2,
                           frame_size[1] // 2 - master_sub_area_size[1] // 2), master_sub_area_size
        binning = max(1, int(binning))

        sub_area_shift = frame_width * (master_sub_area[1][0] / frame_size[0])
        sub_area_height = len(range(master_sub_area[0][0], master_sub_area[0][0] + master_sub_area[1][0], binning))
        sub_area_width = len(range(master_sub_area[0][1], master_sub_area[0][1] + master_sub_area[1][1], binning))

        sub_area = (
            (master_sub_area[0][0] // binning, master_sub_area[0][1] // binning),
            (sub_area_height, sub_area_width),
        )

        return pixel_size, frame_size, frame_width, master_sub_area, master_sub_area_size, sub_area_shift, sub_area

    def acquisition(self,
                    stem_controller: stem_controller_module.STEMController,
                    camera: camera_base.CameraHardwareSource,
                    defocus: float,
                    target_width: tuple[float | int, float | int], timer: bool = False,
                    binning: float = 1.0) -> (tuple[npt.NDArray[numpy.float64], int, float] |
                                              tuple[npt.NDArray[numpy.float64], tuple[tuple[int, int], tuple[int, int]], float, float, float, float, float] |
                                              tuple[int, float] | None):
        """
        Move across the sample in a snake pattern, acquiring images at each position, and return the resulting data and relevant parameters.
        If timer is True, return an estimate of how long the full acquisition will take.

        Args:
        - stem_controller: the instrument used to control the STEM microscope.
        - camera: the Ronchigram camera used to capture images.
        - defocus: the desired defocus value in meters.
        - target_width: the desired width and height of the final image in micrometers.
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

        success, tv_pixel_angle_rad = stem_controller.TryGetVal("TVPixelAngle")  # if success is False, the plugin is likely being run on uSim
        matrix = self.find_matrix()

        if success and matrix is not None and numpy.linalg.det(matrix) != 0 and len(matrix) != 0:  #even if success is True, it could still be on usim- this would give an empty or singular matrix so can guard against non-uSim controls being used
            # this branch will run where the plugin is used on an actual microscope
            shift_x_control_name = "SShft.sx"
            shift_y_control_name = "SShft.sy"

        else:
            #  this allows the plugin to run on uSim
            shift_x_control_name = "stage_position_m.x"
            shift_y_control_name = "stage_position_m.y"

            frame = camera.grab_next_to_start()[0]
            assert frame is not None
            tv_pixel_angle_rad = float(frame.dimensional_calibrations[0].scale)

        # grab stage original location and original defocus
        sx = stem_controller.get_control_output(shift_x_control_name)
        sy = stem_controller.get_control_output(shift_y_control_name)
        df_original = stem_controller.get_control_output("C10")

        assert tv_pixel_angle_rad is not None
        stem_controller.set_control_output("C10", defocus)

        pixel_size, frame_size, frame_width, master_sub_area, master_sub_area_size, sub_area_shift, sub_area = self.find_dimensions(stem_controller, camera, defocus, tv_pixel_angle_rad, binning)

        # calculate the number of frames to cover the target area
        frames_needed_width = math.ceil(target_width[0] * 1e-6 / sub_area_shift)
        frames_needed_height = math.ceil(target_width[1] * 1e-6 / sub_area_shift)
        dimensions = (frames_needed_width, frames_needed_height)

        total_image_height = dimensions[1] * frame_width  # calculate the height of the image in um
        total_images = frames_needed_width * frames_needed_height  # calculate the total number of frames required for the image

        master_data = numpy.empty((sub_area[1][0] * dimensions[0], sub_area[1][1] * dimensions[1]))  # create an empty array to hold the final image data

        if not timer:  # if performing the full acquisition instead of just estimating the time, update the progress bar and output window
            self._append_output_threadsafe(f"Stage starting position: {(sx * 1e6):.3f}, {(sy * 1e6):.3f} um")
            self._append_output_threadsafe(f"Pixel size: {(pixel_size * 1e9):.3f} nm")
            self._append_output_threadsafe(f"Frame width: {(frame_width * 1e6):.3f} um")
            self._append_output_threadsafe(f"Master size: {master_data.shape}\n")

            self._set_progress_threadsafe(0, total_images, "Progress:\nStarting acquisition...")

        t1 = time.time()

        if timer:
            dimensions = (2, 1)  # for timing purposes, only need to acquire 2 frames and average the time to take them both

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

                    if shift_x_control_name == "stage_position_m.x":  # if the plugin is being run on uSim then correction for stage axis is not needed as can move straight along the camera axis
                        delta_x = - sub_area_shift * (column - dimensions[1] // 2)
                        delta_y = - sub_area_shift * (row - dimensions[0] // 2)
                    else:  # if the plugin is being run on a microscope need to transform every movement from the stage axis to the camera axis
                        delta_x = - sub_area_shift * (column - dimensions[1] // 2)
                        delta_y = - sub_area_shift * (row - dimensions[0] // 2)
                        delta_camera = numpy.array([delta_x, delta_y], dtype=numpy.float64)
                        delta_fast = numpy.linalg.solve(matrix, delta_camera)

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

                    #  adding the new frame to the data item
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
            self._set_progress_threadsafe(0, 100, "Progress:\n Idle")  # reset progress bar to idle state

        if timer:
            self.cancel_enabled.value = False
            self.scan_buttons_enabled.value = True
            return master_data, total_images, time_total
        else:
            self.cancel_enabled.value = False
            self.scan_buttons_enabled.value = True
            return master_data, sub_area, sub_area_shift, pixel_size, total_image_height, sx, sy

    def handle_cancel_acquisition_clicked(self, widget: typing.Any) -> None:
        """
        Cancel button: off when the acquisition is not running, on when it is.
        """
        if self._is_running:
            self._cancel_requested = True
            self._set_progress_threadsafe(self.progress_value, 100, "Cancel requested...")


    def handle_estimate_time_clicked(self, widget: typing.Any) -> None:
        """
        Estimates the time an acquisition will take by averaging the time it takes to capture two frames and multiplying by the total number of frames required for the acquisition.
        """
        #  guardrails to make sure width, height, defocus and binning are all integers and within sensible limits
        try:
            width = int(self.width_value)
            height = int(self.height_value)
            defocus = int(self.defocus) * 1e-9
            binning = int(self.binning)
        except ValueError:
            self._append_output("Please enter width, height, binning and defocus as integers.")
            return
        if width < 1 or height < 1 or binning < 1:
            self._append_output("Please ensure width and height are positive.")
            return
        if abs(defocus * 1e9) < 1000 or abs(defocus * 1e9) > 500000:
            self._append_output("Warning: Requested defocus is outside of safe limit")
            return

        stem_controller = self.stem_controller
        camera = self.camera
        target_width = (width, height)

        result = self.acquisition(stem_controller, camera, defocus, target_width, timer=True, binning=binning)
        if result is None or len(result) != 3:
            return

        master_data, total_images, t_total = result
        image_size = master_data.shape
        time_taken = t_total * total_images / 2  # average time to move the stage
        self._append_output(f"This acquisition will take approximately {(time_taken // 3600):.0f}h {((time_taken % 3600) / 60):.0f}min {(time_taken % 60):.0f}s")

        if any(dimension > max_size for dimension in image_size):
            self._append_output("The final data item is too large to be used in the sample navigation window. Consider increasing the binning or reducing the size of the acquisition.\n")
            return
        else:
            self._append_output(f"The final data item will have dimensions {image_size[0]} x {image_size[1]} pixels.\n")
            return

    async def _run_acquisition_async(self,
                                     stem_controller: stem_controller_module.STEMController,
                                     camera: camera_base.CameraHardwareSource,
                                     defocus: float,
                                     target_width: tuple[int, int],
                                     binning: int) -> None:
        """
        Performs acquisition asynchronously to avoid blocking the UI thread, then pushes results to the sample navigation map in AS2.
        Calculates dimensional calibrations for the final data item and creates a new data item in the library.
        Uses REST API calls to get and set the cartridge properties for the sample navigation map.

        Args:
        - stem_controller: the instrument used to control the STEM microscope.
        - camera: the Ronchigram camera used to capture images.
        - defocus: the desired defocus value in meters.
        - binning: the binning factor for the camera, which reduces the resolution of the captured images by combining adjacent pixels.
        """
        loop = self._event_loop

        self._append_output_threadsafe("Starting acquisition...\n")
        try:
            result = await loop.run_in_executor(None, self.acquisition, stem_controller, camera, defocus, target_width, False, binning)
            if result is None or len(result) != 7:
                self._set_progress(0, 100, "Progress:\nIdle")
                return

            master_data, sub_area, sub_area_shift, pixel_size, total_image_height, sx, sy = result
        except Exception as e:
            self._append_output(f"Acquisition failed: {e!r}")
            self.cancel_enabled.value = False
            self.scan_buttons_enabled.value = True
            return

        try:
            # dimensional calibrations for the final data item
            library = self._api.library
            y_scale = (sub_area_shift / sub_area[1][0]) * 1e6
            x_scale = (sub_area_shift / sub_area[1][1]) * 1e6
            dimensional_calibrations = [
                self._api.create_calibration(0.0, y_scale, "um"),
                self._api.create_calibration(0.0, x_scale, "um"),
            ]

            xdata = self._api.create_data_and_metadata(master_data, dimensional_calibrations=dimensional_calibrations)

            # create final data item
            library.create_data_item_from_data_and_metadata(xdata, "Composite Survey")

            self._append_output("Acquisition complete.\n")

            self._append_output("Image properties:")
            self._append_output_threadsafe(f"Total image height: {(total_image_height * 1e3):.3f} mm")
            self._append_output_threadsafe(f"Original stage coordinates: {(sx * 1e6):.3f}, {(sy * 1e6):.3f} um")

            # convert the data to uint8 and save as a jpg
            data_array = numpy.array(xdata)
            data_min = float(numpy.min(data_array))
            data_max = float(numpy.max(data_array))
            data_range = data_max - data_min

            data_uint8 = ((data_array - data_min / data_range * 255).astype(numpy.uint8))

            img = Image.fromarray(data_uint8)
            export_path = Path(r"C:\AS2\AS2User\Pictures\overview-scan.jpg")
            if not export_path.parent.exists():
                export_path.parent.mkdir(parents=True, exist_ok=True)

            img.save(export_path)

        except Exception as e:
            self._append_output(f"Failed to publish result: {e!r}")
            self.cancel_enabled.value = False
            self.scan_buttons_enabled.value = True
            return

        # push the image, scale height and offsets to the sample navigation map
        try:
            cartridge_result = stem_controller._get_rest_api("/exchange?property=CartridgeInStage")
            if cartridge_result.is_valid:
                cartridge_string = cartridge_result.value
                self._append_output_threadsafe(f"Cartridge in stage: {cartridge_string}")
                properties: JSONDict = {"ImageScaleRad_m": total_image_height / 2, "ImageOffsetX_px": sx / pixel_size, "ImageOffsetY_px": sy / pixel_size, "ImageFile": str(export_path)}

                # Set the values on the cartridge

                stem_controller._put_rest_api(f"/exchange/cartridges/{cartridge_string}", content=properties)
                if hasattr(cartridge_result, "is_valid") and not cartridge_result.is_valid:
                    self._append_output_threadsafe(f"PUT failed: {cartridge_result.exception}")
            else:
                self._append_output_threadsafe(f"Failed to get CartridgeInStage: {cartridge_result.exception}")
                return

        except Exception as e:
            self._append_output(f"Failed to update cartridge data: {e!r}")
            self.cancel_enabled.value = False
            self.scan_buttons_enabled.value = True
            return

    def handle_perform_acquisition_clicked(self, widget: typing.Any) -> None:
        """
        Starts the acquisition process by validating input parameters.
        Initiates the asynchronous acquisition task.
        """
        # guardrails to make sure width, height, defocus and binning are all integers and within sensible limits
        try:
            width = int(self.width_value)
            height = int(self.height_value)
            defocus = int(self.defocus) * 1e-9
            binning = int(self.binning)
        except ValueError:
            self._append_output("Please enter width, height, binning and defocus as integers.")
            return

        if width < 1 or height < 1 or binning < 1:
            self._append_output("Please ensure width and height are positive.")
            return
        if abs(defocus * 1e9) < 1000 or abs(defocus * 1e9) > 500000:
            self._append_output("Warning: Requested defocus is outside of safe limit")
            return

        if self._acq_task and not self._acq_task.done():
            self._append_output("Acquisition already running.")
            return

        stem_controller = self.stem_controller
        camera = self.camera
        target_width = (width, height)

        self._acq_task = self._event_loop.create_task(
            self._run_acquisition_async(stem_controller, camera, defocus, target_width, binning)
        )
        self.cancel_enabled.value = False
        self.scan_buttons_enabled.value = True

    def handle_max_clicked(self, widget: typing.Any) -> None:
        """
        Calculates the maximum scan size at the specified defocus/binning for the image to be pushed to the sample navigation map.
        Estimates the time it will take and performs the acquisition.
        """
        # guardrails to make sure defocus and binning are both integers and within sensible limits
        try:
            defocus = int(self.defocus) * 1e-9
            binning = int(self.binning)
        except ValueError:
            self._append_output("Please enter defocus and binning as integers.")
            return
        if abs(defocus * 1e9) < 1000 or abs(defocus * 1e9) > 500000:
            self._append_output("Warning: Requested defocus is outside of sensible limit")
            return

        if self._acq_task and not self._acq_task.done():
            self._append_output("Acquisition already running.")
            return

        stem_controller = self.stem_controller
        camera = self.camera

        # calculating the maximum scan size at the specified defocus/binning for the image to be pushed to the sample navigation map
        success, tv_pixel_angle_rad = stem_controller.TryGetVal("TVPixelAngle")

        if not success:
            frame = camera.grab_next_to_start()[0]
            assert frame is not None
            tv_pixel_angle_rad = float(frame.dimensional_calibrations[0].scale)

        assert tv_pixel_angle_rad is not None

        pixel_size, frame_size, frame_width, master_sub_area, master_sub_area_size, sub_area_shift, sub_area = self.find_dimensions(stem_controller, camera, defocus, tv_pixel_angle_rad, binning)

        dimension_y = max_size // sub_area[1][0]
        dimension_x = max_size // sub_area[1][1]

        # putting the calculated maximum scan size into the width and height fields in the UI
        self.width_value = str(int(dimension_x * sub_area_shift * 1e6))
        self.height_value = str(int(dimension_y * sub_area_shift * 1e6))
        self.property_changed_event.fire("width_value")
        self.property_changed_event.fire("height_value")

    def handle_clear_minimap_clicked(self, widget: typing.Any) -> None:
        """
        Clears the image, scale height and offsets from the sample navigation map.
        """
        stem_controller = self.stem_controller
        try:
            cartridge_result = stem_controller._get_rest_api("/exchange?property=CartridgeInStage")
            if cartridge_result.is_valid:
                cartridge_string = cartridge_result.value
                properties: JSONDict = {"ImageScaleRad_m": 0.0, "ImageOffsetX_px": 0.0, "ImageOffsetY_px": 0.0, "ImageFile": ""}
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