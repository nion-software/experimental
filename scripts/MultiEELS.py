import numpy
import uuid
import _autostem
import time
from nion.swift.model import DataItem
from nion.swift.model import HardwareSource
from nion.swift.model import ImportExportManager
from nion.data import Calibration
from nion.data import DataAndMetadata
from nion.data import Image
from nion.data import xdata_1_0 as xd

def acquire_multi_eels(interactive, api):
    hw_cam = HardwareSource.HardwareSourceManager().get_hardware_source_for_hardware_source_id("eels_tuning_camera")
    hw_cam.stop_playing()

    table = [
    # energy offset, exposure(ms), N frames
      (0, 3000, 10),
      #(0, 50, 1),
      #(0, 100, 1),
      #(0, 100, 5),
      #(0, 100, 20),
      #(0, 100, 100),
    ]

   # table = [
    #  (0, 10, 5),
     # (100, 10, 5),
      #(200, 100, 5),
      #(400, 1000, 5),
      # (0, 1, 5),
    #]

    spectra = list()

    intensity_scale0 = None

    do_dark = True
    for offset, exposure, frames in table:
        _autostem.SetValAndConfirm("DriftTubeLoss", offset, 1.0, 3000)
        fp = hw_cam.get_current_frame_parameters()
        fp.exposure_ms = exposure
        hw_cam.set_current_frame_parameters(fp)
        _autostem.SetValAndConfirm("C_Blank", 0, 1.0, 3000)
        hw_cam.acquire_sequence_prepare(frames)
        print(hw_cam.acquire_sequence(frames)[0]["data"].shape)
        hw_cam.acquire_sequence_prepare(frames)
        xdata = ImportExportManager.convert_data_element_to_data_and_metadata(hw_cam.acquire_sequence(frames)[0])
        counts_per_electron = xdata.metadata.get("hardware_source", dict()).get("counts_per_electron", 1)
        exposure = xdata.metadata.get("hardware_source", dict()).get("exposure", 1)
        intensity_scale = xdata.intensity_calibration.scale / counts_per_electron / xdata.dimensional_calibrations[-1].scale / exposure / frames
        if not intensity_scale0:
            intensity_scale0 = intensity_scale
        xdata = xd.sum(xdata, 0)
        if do_dark:
            _autostem.SetValAndConfirm("C_Blank", 1, 1.0, 3000)
            #time.sleep(10)
            hw_cam.acquire_sequence_prepare(frames)
            print(hw_cam.acquire_sequence(frames)[0]["data"].shape)
            hw_cam.acquire_sequence_prepare(frames)
            data_element = hw_cam.acquire_sequence(frames)[0]
            dark_xdata = ImportExportManager.convert_data_element_to_data_and_metadata(data_element)
            dark_xdata = xd.sum(dark_xdata, 0)
            xdata = xdata - dark_xdata
        if False:  # do gain?
            # divide out the gain
            gain = interactive.document_controller.document_model.get_data_item_by_uuid(uuid.UUID("1762237d-4da4-4074-9d5d-f147fce2d999"))
            #interactive.show_xdata(xd.sum(gain.xdata, 0))
            #interactive.show_xdata(xd.sum(xdata, 0))  # this is the data from the scope
            gain_average = numpy.mean(gain.data)
            igain = gain.xdata / gain_average
            xdata = xdata / gain.xdata
        spectrum = xd.sum(xdata, 0)
        # multiplying by intensity scale ratio to work around bug in composite item display.
        # no longer needed. fixed in Swift.
        # spectrum = spectrum * (intensity_scale / intensity_scale0)
        spectrum.data_metadata._set_intensity_calibration(Calibration.Calibration(scale=intensity_scale, units="e/eV/s"))
        spectrum.data_metadata._set_metadata({"title": f"{offset}eV {int(exposure*1000)}ms [x{frames}]"})
        spectra.append(spectrum)
    _autostem.SetValAndConfirm("C_Blank", 0, 1.0, 3000)
    _autostem.SetValAndConfirm("DriftTubeLoss", 0, 1.0, 3000)
    print("finished taking data")

    if len(spectra) > 0:
        def construct():
            data_items = list()
            for spectrum in spectra:
                data_item = DataItem.new_data_item(spectrum)
                data_item.title = spectrum.metadata["title"]
                interactive.document_controller.document_model.append_data_item(data_item)
                data_items.append(data_item)
            composite_data_item = DataItem.CompositeLibraryItem()
            for data_item in data_items:
                composite_data_item.append_data_item(data_item)
            composite_display_specifier = DataItem.DisplaySpecifier.from_data_item(composite_data_item)
            composite_display_specifier.display.display_type = "line_plot"
            composite_display_specifier.display.dimensional_scales = (spectra[0].dimensional_shape[-1], )
            composite_display_specifier.display.dimensional_calibrations = (spectra[0].dimensional_calibrations[-1], )
            composite_display_specifier.display.intensity_calibration = spectra[0].intensity_calibration
            composite_display_specifier.display.legend_labels = [data_item.title for data_item in data_items]
            interactive.document_controller.document_model.append_data_item(composite_data_item)
        interactive.document_controller.queue_task(construct)
    print("finished")


def script_main(api_broker):
    interactive = api_broker.get_interactive(version="1")
    interactive.print_debug = interactive.print
    api = api_broker.get_api(version="~1.0")
    acquire_multi_eels(interactive, api)
    
