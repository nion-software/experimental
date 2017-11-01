from nion.data import Calibration
from nion.data import DataAndMetadata
from nion.data import xdata_1_0 as xd

import numpy

def measure_shifts(interactive, api):
    print("Starting measure shifts...")

    # first find the target SI from which to measure shifts
    src = api.application.document_windows[0].target_data_item.xdata

    # save the x calibration; and energy calibration
    xc = src.dimensional_calibrations[1]
    ec = src.dimensional_calibrations[2]

    # convert the SI to a sequence ordered by x- and the y- direction
    data_descriptor = DataAndMetadata.DataDescriptor(True, 0, 1)
    src_seq = DataAndMetadata.new_data_and_metadata(numpy.reshape(src.data, (src.data_shape[0] * src.data_shape[1], src.data_shape[-1])), data_descriptor=data_descriptor)

    # from the sequence, measure the relative shift between each successive frame
    m = xd.sequence_register_translation(src_seq, 100)

    # measuring shift will produce a n x 1 array; squeeze the data to get rid of the unnecessary dimension
    md = numpy.squeeze(m.data)

    # since sequence_register measures the shift between each frame, integrate it to produce absolute shift
    # and also note that register_translation produces negative values for historical/compatibility reasons (scikit).
    md = numpy.cumsum(-md)

    # grab the pixel time if possible.
    pixel_time_us = src.metadata.get("scan_detector", dict()).get("pixel_time_us")

    if not pixel_time_us:
        pixel_time_us = interactive.get_integer("Pixel time (us):")

    # provide calibrations if possible
    dimensional_calibrations = [Calibration.Calibration(scale=pixel_time_us / 1E6, units="s")]
    intensity_calibration = Calibration.Calibration(scale=ec.scale, units=ec.units)

    # construct the data item
    xmd = DataAndMetadata.new_data_and_metadata(md, intensity_calibration=intensity_calibration, dimensional_calibrations=dimensional_calibrations)
    data_item = api.library.create_data_item_from_data_and_metadata(xmd)

    # and show the data item
    api.application.document_windows[0].display_data_item(data_item)

    print("Finished measure shifts...")


def script_main(api_broker):
    interactive = api_broker.get_interactive(version="1")
    api = api_broker.get_api(version="1")
    measure_shifts(interactive, api)
