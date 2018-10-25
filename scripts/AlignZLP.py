import numpy
from nion.data import DataAndMetadata

def align_zlp(interactive, api):
    window = api.application.document_windows[0]
    target_data_item = window.target_data_item
    if target_data_item:
        target_xdata = target_data_item.xdata
        if target_xdata.data_descriptor == DataAndMetadata.DataDescriptor(False, 2, 1):
            src_data = target_data_item.data
            dst_data = numpy.zeros(src_data.shape)
            ref_pos = numpy.argmax(src_data[0, 0])
            for r in range(src_data.shape[0]):
                for c in range(src_data.shape[1]):
                    if True:
                        mx_pos = numpy.argmax(src_data[r, c])
                        offset = mx_pos - ref_pos
                        if offset < 0:
                            dst_data[r, c][-offset:] = src_data[r, c][0:offset]
                        elif offset > 0:
                            dst_data[r, c][:-offset] = src_data[r, c][offset:]
                        else:
                            dst_data[r, c][:] = src_data[r, c][:]
                    # print(f"{r},{c}: {dst_data[r, c]}")
                print(f"row {r}")
                if interactive.cancelled:
                    break
            dst_xdata = DataAndMetadata.new_data_and_metadata(dst_data, target_xdata.intensity_calibration, target_xdata.dimensional_calibrations, data_descriptor=DataAndMetadata.DataDescriptor(False, 2, 1))
            data_item = api.library.create_data_item_from_data_and_metadata(dst_xdata)
            data_item.title = "Aligned " + target_data_item.title
            window.display_data_item(data_item)


def script_main(api_broker):
    interactive = api_broker.get_interactive(version="1")
    interactive.print_debug = interactive.print
    api = api_broker.get_api(version="~1.0")
    align_zlp(interactive, api)
