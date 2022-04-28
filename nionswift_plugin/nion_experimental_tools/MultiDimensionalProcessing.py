import typing
import gettext
import copy
import numpy
import scipy.ndimage
import time
import multiprocessing
import threading
import logging

from nion.data import Core
from nion.data import DataAndMetadata
from nion.data import Calibration
from nion.swift.model import Symbolic
from nion.swift.model import DataItem
from nion.swift import Inspector
from nion.swift import DocumentController
from nion.ui import Declarative
from nion.utils import Registry
from nion.utils import Observable
from nion.typeshed import API_1_0 as API

try:
    import mkl
except ModuleNotFoundError:
    _has_mkl = False
else:
    _has_mkl = True

_ = gettext.gettext


class IntegrateAlongAxis(Symbolic.ComputationHandlerLike):
    label = _("Integrate")
    inputs = {"input_data_item": {"label": _("Input data item")},
              "axes_description": {"label": _("Integrate these axes")},
              # "sub_integration_axes": {"label": _("Select which of the above axes to integrate"), "entity_id": "sub_axis_choice"},
              "integration_graphic": {"label": _("Integration mask")},
              }
    outputs = {"integrated": {"label": _("Integrated")},
               }

    def __init__(self, computation, **kwargs):
        self.computation = computation

    @staticmethod
    def guess_starting_axis(data_item: DataItem.DataItem, graphic: typing.Optional[API.Graphic] = None) -> str:
        # If we have an integrate graphic we probably want to integrate the displayed dimensions
        if graphic:
            # For collections with 1D data we see the collection dimensions
            if data_item.xdata.is_collection and data_item.xdata.datum_dimension_count == 1:
                integration_axes = "collection"
            # Otherwise we see the data dimensions
            else:
                integration_axes = "data"
        # If not, use some generic rules
        else:
            if data_item.xdata.is_sequence:
                integration_axes = "sequence"
            elif data_item.xdata.is_collection and data_item.xdata.datum_dimension_count == 1:
                integration_axes = "collection"
            else:
                integration_axes = "data"

        return integration_axes

    def execute(self, input_data_item: API.DataItem, axes_description: str, integration_graphic: typing.Optional[API.Graphic]=None):
        input_xdata: DataAndMetadata.DataAndMetadata = input_data_item.xdata
        split_description = axes_description.split("-")
        integration_axes = split_description[0]
        sub_integration_axes = split_description[1] if len(split_description) > 1 else "all"
        if integration_axes == "collection":
            assert input_xdata.is_collection
            integration_axis_indices = list(input_xdata.collection_dimension_indexes)
            integration_axis_shape = input_xdata.collection_dimension_shape
            result_data_descriptor = DataAndMetadata.DataDescriptor(input_xdata.is_sequence, 0, input_xdata.datum_dimension_count)
            if sub_integration_axes != "all" and input_xdata.collection_dimension_count > 1:
                index = ["first", "second"].index(sub_integration_axes)
                integration_axis_indices = [integration_axis_indices[index]]
                integration_axis_shape = (integration_axis_shape[index],)
                result_data_descriptor = DataAndMetadata.DataDescriptor(input_xdata.is_sequence, input_xdata.collection_dimension_count - 1, input_xdata.datum_dimension_count)
        elif integration_axes == "sequence":
            assert input_xdata.is_sequence
            integration_axis_indices = [input_xdata.sequence_dimension_index]
            integration_axis_shape = input_xdata.sequence_dimension_shape
            result_data_descriptor = DataAndMetadata.DataDescriptor(False, input_xdata.collection_dimension_count, input_xdata.datum_dimension_count)
        else:
            integration_axis_indices = list(input_xdata.datum_dimension_indexes)
            integration_axis_shape = input_xdata.datum_dimension_shape
            # 0-D data is not allowed in Swift, so we need to make the collection or the sequence axis the data axis
            # Use the collection axis preferably and only when the data is not a collection use the sequence axis
            # If the user integrated a single image we get a single number. We also make this 1D data to prevent errors
            if input_xdata.is_collection:
                result_data_descriptor = DataAndMetadata.DataDescriptor(input_xdata.is_sequence, 0, input_xdata.collection_dimension_count)
            else:
                result_data_descriptor = DataAndMetadata.DataDescriptor(False, 0, 1)

            if sub_integration_axes != "all" and input_xdata.datum_dimension_count > 1:
                index = ["first", "second"].index(sub_integration_axes)
                integration_axis_indices = [integration_axis_indices[index]]
                integration_axis_shape = (integration_axis_shape[index],)
                result_data_descriptor = DataAndMetadata.DataDescriptor(input_xdata.is_sequence, input_xdata.collection_dimension_count, input_xdata.datum_dimension_count - 1)

        navigation_shape = []
        navigation_axis_indices = []
        for i in range(len(input_xdata.data_shape)):
            if not i in integration_axis_indices:
                navigation_shape.append(input_xdata.data_shape[i])
                navigation_axis_indices.append(i)

        data_str = ''
        mask_str = ''
        navigation_str = ''
        for i in range(len(input_xdata.data_shape)):
            char = chr(i + 97)
            data_str += char
            if i in integration_axis_indices:
                mask_str += char
            else:
                navigation_str += char
        # chr(97) == 'a' so we get letters in alphabetic order here (a, b, c, d, ...)
        sum_str = ''.join([chr(i + 97) for i in range(len(integration_axis_shape))])
        operands = [input_xdata.data]
        if integration_graphic is not None:
            mask = integration_graphic.mask_xdata_with_shape(integration_axis_shape)
            operands.append(mask)
            sum_str = data_str + ',' + mask_str
        else:
            sum_str = data_str + '->' + navigation_str
        result_data = numpy.einsum(sum_str, *operands)

        # result_data = numpy.empty(navigation_shape, dtype=input_xdata.data_dtype)

        # last_reported = time.time()
        # n_images = numpy.prod(navigation_shape, dtype=numpy.int64)
        # load_time = 0
        # process_time = 0
        # starttime = time.time()
        # for i in range(n_images):
        #     coords = numpy.unravel_index(i, navigation_shape)
        #     data_coords = coords[:integration_axis_indices[0]] + (...,) + coords[integration_axis_indices[0]:]
        #     t0 = time.perf_counter()
        #     operands[0] = input_xdata.data[data_coords]
        #     t1 = time.perf_counter()
        #     result_data[coords] = numpy.einsum(sum_str, *operands)
        #     t2 = time.perf_counter()
        #     load_time += t1 - t0
        #     process_time += t2 - t1
        #     now = time.time()
        #     if now - last_reported > 3.0:
        #         last_reported = now
        #         print(f"Processed {i}/{n_images} data points ({i/(now - starttime):.0f} dpps). Spent {load_time:.1f} s loading data and {process_time:.1f} s processing data so far.")

        result_dimensional_calibrations = []
        for i in range(len(input_xdata.data_shape)):
            if not i in integration_axis_indices:
                result_dimensional_calibrations.append(input_xdata.dimensional_calibrations[i])

        result_data = numpy.atleast_1d(result_data)

        if len(result_dimensional_calibrations) != len(result_data.shape):
            result_dimensional_calibrations = [Calibration.Calibration() for _ in range(len(result_data.shape))]

        self.__result_xdata = DataAndMetadata.new_data_and_metadata(result_data,
                                                                    intensity_calibration=input_xdata.intensity_calibration,
                                                                    dimensional_calibrations=result_dimensional_calibrations,
                                                                    data_descriptor=result_data_descriptor)

    def commit(self):
        self.computation.set_referenced_xdata("integrated", self.__result_xdata)


def ellipse_radius(polar_angle: typing.Union[float, numpy.ndarray], a: float, b: float, rotation: float) -> typing.Union[float, numpy.ndarray]:
    """
    Returns the radius of a point lying on an ellipse with the given parameters. The ellipse is described in polar
    coordinates here, which makes it easy to incorporate a rotation.
    Parameters
    -----------
    polar_angle : float or numpy.ndarray
                  Polar angle of a point to which the corresponding radius should be calculated (rad).
    a : float
        Length of the major half-axis of the ellipse.
    b : float
        Length of the minor half-axis of the ellipse.
    rotation : Rotation of the ellipse with respect to the x-axis (rad). Counter-clockwise is positive.
    Returns
    --------
    radius : float or numpy.ndarray
             Radius of a point lying on an ellipse with the given parameters.
    """

    return a*b/numpy.sqrt((b*numpy.cos(polar_angle+rotation))**2+(a*numpy.sin(polar_angle+rotation))**2)


def draw_ellipse(image: numpy.ndarray, ellipse: typing.Tuple[float, float, float, float, float], *,
                 color: typing.Any=1.0) -> None:
    """
    Draws an ellipse on a 2D-array.
    Parameters
    ----------
    image : array
            The array on which the ellipse will be drawn. Note that the data will be modified in place.
    ellipse : tuple
              A tuple describing an ellipse. The values must be (in this order):
              [0] The y-coordinate of the center.
              [1] The x-coordinate of the center.
              [2] The length of the major half-axis
              [3] The length of the minor half-axis
              [4] The rotation of the ellipse in rad.
    color : optional
            The color to which the pixels inside the given ellipse will be set. Note that `color` will be cast to the
            type of `image` automatically. If this is not possible, an exception will be raised. The default is 1.0.
    Returns
    --------
    None
    """
    shape = image.shape
    assert len(shape) == 2, 'Can only draw an ellipse on a 2D-array.'

    top = max(int(ellipse[0] - ellipse[2]), 0)
    left = max(int(ellipse[1] - ellipse[2]), 0)
    bottom = min(int(ellipse[0] + ellipse[2]) + 1, shape[0])
    right = min(int(ellipse[1] + ellipse[2]) + 1, shape[1])
    coords = numpy.mgrid[top - ellipse[0]:bottom - ellipse[0], left - ellipse[1]:right - ellipse[1]] # type: ignore # Not working yet, see https://github.com/python/mypy/issues/2410
    radii = numpy.sqrt(numpy.sum(coords**2, axis=0))
    polar_angles = numpy.arctan2(coords[0], coords[1])
    ellipse_radii = ellipse_radius(polar_angles, *ellipse[2:])
    image[top:bottom, left:right][radii<ellipse_radii] = color


def _make_mask(max_shift: int, origin: typing.Tuple[int, ...], data_shape: typing.Tuple[int, ...]) -> numpy.ndarray:
    mask = numpy.zeros(data_shape, dtype=bool)
    if len(data_shape) == 2:
        half_shape = (data_shape[0] // 2, data_shape[1] // 2)
        offset = (origin[0] + half_shape[0], origin[1] + half_shape[1])
        draw_ellipse(mask, offset + (max_shift, max_shift, 0))
    elif len(data_shape) == 1:
        half_shape = data_shape[0] // 2
        mask[max(0, origin[0] + half_shape - max_shift):min(data_shape[0], origin[0] + half_shape + max_shift + 1)] = True
    else:
        raise ValueError('Only data of 1 or 2 dimensions is allowed.')
    return mask


def function_measure_multi_dimensional_shifts(xdata: DataAndMetadata.DataAndMetadata,
                                              shift_axis: str,
                                              reference_index: typing.Union[None, int, typing.Sequence[int]]=None,
                                              bounds: typing.Any=None,
                                              max_shift: typing.Optional[int] = None,
                                              origin: typing.Tuple[int, ...] = (0, 0)) -> DataAndMetadata.DataAndMetadata:
    """
    "max_shift" defines the maximum allowed template shift in pixels. "max_shift" is calculated around "origin", which
    is the offset from the center of the image.
    """
    if shift_axis == "collection":
        assert xdata.is_collection
        if xdata.collection_dimension_count == 2:
            shifts_ndim = 1
        else:
            shifts_ndim = 0
        shift_axis_indices = list(xdata.collection_dimension_indexes)
    elif shift_axis == "sequence":
        assert xdata.is_sequence
        shifts_ndim = 0
        shift_axis_indices = [xdata.sequence_dimension_index]
    elif shift_axis == "data":
        if xdata.datum_dimension_count == 2:
            shifts_ndim = 1
        else:
            shifts_ndim = 0
        shift_axis_indices = list(xdata.datum_dimension_indexes)
    else:
        raise ValueError(f"Unknown shift axis: '{shift_axis}'.")

    iteration_shape: typing.Tuple[int, ...] = tuple()
    dimensional_calibrations = list()
    intensity_calibration = None
    for i in range(len(xdata.data_shape)):
        if not i in shift_axis_indices:
            iteration_shape += (xdata.data_shape[i],)
            dimensional_calibrations.append(xdata.dimensional_calibrations[i])
        else:
            intensity_calibration = Calibration.Calibration(scale=xdata.dimensional_calibrations[i].scale, units=xdata.dimensional_calibrations[i].units)

    shape: typing.Tuple[int, ...]
    register_slice: typing.Union[slice, typing.Tuple[slice, slice]]

    if shifts_ndim == 1:
        result_shape = iteration_shape + (2,)
        dimensional_calibrations.append(Calibration.Calibration())
        if bounds is not None:
            assert numpy.ndim(bounds) == 2
            shape = (xdata.data_shape[shift_axis_indices[0]], xdata.data_shape[shift_axis_indices[1]])
            register_slice = (slice(max(0, int(round(bounds[0][0] * shape[0]))), min(int(round((bounds[0][0] + bounds[1][0]) * shape[0])), shape[0])),
                              slice(max(0, int(round(bounds[0][1] * shape[1]))), min(int(round((bounds[0][1] + bounds[1][1]) * shape[1])), shape[1])))
        else:
            register_slice = (slice(0, None), slice(0, None))
    else:
        result_shape = iteration_shape + (1,)
        if bounds is not None:
            assert numpy.ndim(bounds) == 1
            shape = (xdata.data_shape[shift_axis_indices[0]],)
            register_slice = slice(max(0, int(round(bounds[0] * shape[0]))), min(int(round(bounds[1] * shape[0])), shape[0]))
        else:
            register_slice = slice(0, None)

    reference_data = None
    if reference_index is not None:
        if numpy.isscalar(reference_index):
            coords = numpy.unravel_index(reference_index, iteration_shape)
        else:
            coords = reference_index
        data_coords = coords[:shift_axis_indices[0]] + (...,) + coords[shift_axis_indices[0]:]
        reference_data = xdata.data[data_coords]

    mask = None
    # If we measure shifts relative to the last frame, we can always use a mask that is centered around the input origin
    if max_shift is not None and reference_index is None:
        coords = numpy.unravel_index(0, iteration_shape)
        data_coords = coords[:shift_axis_indices[0]] + (...,) + coords[shift_axis_indices[0]:]
        data_shape = xdata.data[data_coords][register_slice].shape
        mask = _make_mask(max_shift, origin, data_shape)

    shifts = numpy.zeros(result_shape, dtype=numpy.float32)
    start_index = 0 if reference_index is not None else 1
    navigation_len = numpy.prod(iteration_shape, dtype=numpy.int64)
    num_cpus = 8
    try:
        num_cpus = multiprocessing.cpu_count()
    except NotImplementedError:
        logging.warning('Could not determine the number of CPU cores. Defaulting to 8.')
    finally:
        # Use a little bit more than half the CPU cores, but not more than 20 because then we actually get a slowdown
        # because of our HDF5 storage handler not being able to grant parallel access to the data
        num_threads = min(int(round(num_cpus * 0.6)), 20)

    # Unfortunately multi-threading cannot be used when we cross-correlate with the first frame and max_shift
    # is not None because in order to create the mask for each frame we need the shift from the previous frame.
    # This does not work for the "block boundaries" where the data is split between the frames, so we have to
    # do a single-threaded calculation in this case.
    # If the shifts reference is not the first or last frame in the sequence, we can actually use two threads, both
    # starting from reference_index and iterating away from it.
    if max_shift is not None and reference_index is not None:
        if reference_index == 0 or reference_index == navigation_len - 1:
            sections = [start_index, navigation_len]
        else:
            sections = [start_index, reference_index, navigation_len]
    else:
        sections = list(range(start_index, navigation_len, max(1, navigation_len//num_threads)))
        sections.append(navigation_len)
    barrier = threading.Barrier(len(sections))

    def run_on_thread(range_):
        if _has_mkl:
            mkl.set_num_threads_local(1)
        local_mask = mask
        local_reference_data = reference_data
        try:
            for i in range_:
                coords = numpy.unravel_index(i, iteration_shape)
                data_coords = coords[:shift_axis_indices[0]] + (...,) + coords[shift_axis_indices[0]:]
                if reference_index is None:
                    coords_ref = numpy.unravel_index(i - range_.step, iteration_shape)
                    data_coords_ref = coords_ref[:shift_axis_indices[0]] + (...,) + coords_ref[shift_axis_indices[0]:]
                    local_reference_data = xdata.data[data_coords_ref]
                elif max_shift is not None and i != range_.start:
                    last_coords = numpy.unravel_index(i - range_.step, iteration_shape)
                    last_shift = shifts[last_coords]
                    data_shape = local_reference_data[register_slice].shape
                    if len(data_shape) == 2:
                        local_mask = _make_mask(max_shift, (origin[0] + round(last_shift[0]), origin[1] + round(last_shift[1])), data_shape)
                    else:
                        local_mask = _make_mask(max_shift, (origin[0] + round(last_shift[0]),), data_shape)
                shifts[coords] = Core.function_register_template(local_reference_data[register_slice], xdata.data[data_coords][register_slice], ccorr_mask=local_mask)[1]
        finally:
            barrier.wait()

    if max_shift is not None and reference_index is not None:
        # Set up the threads for the case with max_shift and reference index: As explained above, we need a special
        # setup because the result relies on the previous shift.
        if len(sections) == 2:
            if reference_index == 0:
                threading.Thread(target=run_on_thread, args=(range(sections[0], sections[1]),)).start()
            # Reference index is the last frame, so go backwards from there
            else:
                threading.Thread(target=run_on_thread, args=(range(sections[1] - 1, sections[0] - 1, -1),)).start()
        else:
            # If the reference index is somewhere inside the sequence, we can use two threads, one going from
            # reference_index to 0 (backwards) and one gaing from reference_index to the end.
            threading.Thread(target=run_on_thread, args=(range(sections[1], sections[0] - 1, -1),)).start()
            threading.Thread(target=run_on_thread, args=(range(sections[1], sections[2]),)).start()
    else:
        for i in range(len(sections) - 1):
            threading.Thread(target=run_on_thread, args=(range(sections[i], sections[i+1]),)).start()
    barrier.wait()

    # For debugging it is helpful to run a non-threaded version of the code. Comment out the 3 lines above and uncomment
    # the line below to do so. You also need to comment out "barrier.wait()" in the function running on the thread.
    # run_on_thread(range(start_index, navigation_len))

    shifts = numpy.squeeze(shifts)

    if reference_index is None:
        if len(iteration_shape) == 2:
            shifts = numpy.cumsum(shifts, axis=1)
        shifts = numpy.cumsum(shifts, axis=0)

    return DataAndMetadata.new_data_and_metadata(shifts,
                                                 intensity_calibration=intensity_calibration,
                                                 dimensional_calibrations=dimensional_calibrations)


def function_apply_multi_dimensional_shifts(xdata: DataAndMetadata.DataAndMetadata,
                                            shifts: numpy.ndarray,
                                            shift_axis: str,
                                            out: typing.Optional[DataAndMetadata.DataAndMetadata] = None) -> typing.Optional[DataAndMetadata.DataAndMetadata]:
    if shift_axis == "collection":
        assert xdata.is_collection
        if xdata.collection_dimension_count == 2:
            assert shifts.shape[-1] == 2
            shifts_shape = shifts.shape[:-1]
        else:
            shifts_shape = shifts.shape
        shift_axis_indices = list(xdata.collection_dimension_indexes)
    elif shift_axis == "sequence":
        assert xdata.is_sequence
        shifts_shape = shifts.shape
        shift_axis_indices = [xdata.sequence_dimension_index]
    elif shift_axis == "data":
        if xdata.datum_dimension_count == 2:
            assert shifts.shape[-1] == 2
            shifts_shape = shifts.shape[:-1]
        else:
            shifts_shape = shifts.shape
        shift_axis_indices = list(xdata.datum_dimension_indexes)
    else:
        raise ValueError(f"Unknown shift axis: '{shift_axis}'.")

    # Find the axes that we do not want to shift (== iteration shape)
    iteration_shape: typing.Tuple[int, ...] = tuple()
    iteration_shape_offset = 0
    for i in range(len(xdata.data_shape)):
        if not i in shift_axis_indices:
            iteration_shape += (xdata.data_shape[i],)
        elif len(iteration_shape) == 0:
            iteration_shape_offset += 1

    # Now we need to find matching axes between the iteration shape and the provided shifts. We can then iterate over
    # these matching axis and apply the shifts.
    for i in range(len(iteration_shape) - len(shifts_shape) + 1):
        if iteration_shape[i:i+len(shifts_shape)] == shifts_shape:
            shifts_start_axis = i
            shifts_end_axis = i + len(shifts_shape)
            break
    else:
        raise ValueError("Did not find any axis matching the shifts shape.")

    # Now drop all iteration axes after the last shift axis. This will greatly improve speed because we don't have
    # to iterate and shift each individual element but can work in larger sub-arrays. It will also be beneficial for
    # working with chunked hdf5 files because we usually have our chunks along the last axes.
    squeezed_iteration_shape = iteration_shape[:shifts_end_axis]
    # Chunking it up finer (still aligned with chunks on disk) does not make it faster (actually slower by about a factor
    # of 3). This might change with a storage handler that allows multi-threaded access but for now with h5py we don't
    # want to use this.
    # squeezed_iteration_shape = iteration_shape[:max(shifts_end_axis, shift_axis_indices[0])]

    if out is None:
        result = numpy.empty(xdata.data_shape, dtype=xdata.data_dtype)
    else:
        result = out.data

    # for i in range(numpy.prod(squeezed_iteration_shape, dtype=numpy.int64)):
    #     coords = numpy.unravel_index(i, squeezed_iteration_shape)
    #     for i, ind in enumerate(shift_axis_indices):
    #         shifts_array[ind - len(squeezed_iteration_shape)] = shifts[coords][i]
    #     result[coords] = scipy.ndimage.shift(xdata.data[coords], shifts_array, order=1)

    navigation_len = numpy.prod(squeezed_iteration_shape, dtype=numpy.int64)
    sections = list(range(0, navigation_len, max(1, navigation_len//8)))
    sections.append(navigation_len)
    barrier = threading.Barrier(len(sections))

    def run_on_thread(range_):
        try:
            shifts_array = numpy.zeros(len(shift_axis_indices) + (len(iteration_shape) - len(squeezed_iteration_shape)))
            if shifts_end_axis < len(shifts.shape):
                for i in range_:
                    coords = numpy.unravel_index(i, squeezed_iteration_shape)
                    shift_coords = coords[:shifts_end_axis]
                    for j, ind in enumerate(shift_axis_indices):
                        shifts_array[ind - len(squeezed_iteration_shape)] = shifts[shift_coords][j]
                    # if i % max((range_.stop - range_.start) // 4, 1) == 0:
                    #     print(f'Working on slice {coords}: shifting by {shifts_array}')
                    result[coords] = scipy.ndimage.shift(xdata.data[coords], shifts_array, order=1)
            # Note: Once we have multi-dimensional sequences, we need and implementation for iteration_shape_offset != 0
            # and shifts for more than 1-D data (so similar to the loop above but with offset)
            elif iteration_shape_offset != 0:
                offset_slices = tuple([slice(None) for _ in range(iteration_shape_offset)])
                for i in range_:
                    shift_coords = numpy.unravel_index(i, squeezed_iteration_shape)
                    coords = offset_slices + shift_coords
                    shifts_array[0] = shifts[shift_coords]
                    result[coords] = scipy.ndimage.shift(xdata.data[coords], shifts_array, order=1)
            else:
                for i in range_:
                    coords = numpy.unravel_index(i, squeezed_iteration_shape)
                    shifts_array[0] = shifts[coords]
                    result[coords] = scipy.ndimage.shift(xdata.data[coords], shifts_array, order=1)
        finally:
            barrier.wait()

    for i in range(len(sections) - 1):
        threading.Thread(target=run_on_thread, args=(range(sections[i], sections[i+1]),)).start()
    barrier.wait()
    # For debugging it is helpful to run a non-threaded version of the code. Comment out the 3 lines above and uncomment
    # the line below to do so. You also need to comment out "barrier.wait()" in the function running on the thread.
    # run_on_thread(range(0, navigation_len))

    if out is None:
        return DataAndMetadata.new_data_and_metadata(result,
                                                     intensity_calibration=xdata.intensity_calibration,
                                                     dimensional_calibrations=xdata.dimensional_calibrations,
                                                     metadata=xdata.metadata,
                                                     data_descriptor=xdata.data_descriptor)


def function_make_tableau_image(xdata: DataAndMetadata.DataAndMetadata,
                                scale: float = 1.0) -> DataAndMetadata.DataAndMetadata:
    assert xdata.is_collection or xdata.is_sequence
    assert xdata.datum_dimension_count == 2

    iteration_shape: typing.Tuple[int, ...] = tuple()
    tableau_shape: typing.Tuple[int, ...] = tuple()
    iteration_start_index: typing.Optional[int] = None
    if xdata.is_collection:
        iteration_shape = tuple([xdata.data.shape[index] for index in xdata.collection_dimension_indexes])
        iteration_start_index = xdata.collection_dimension_indexes[0]
        data_descriptor = DataAndMetadata.DataDescriptor(xdata.is_sequence, 0, 2)
    elif xdata.is_sequence:
        iteration_shape = (xdata.data.shape[xdata.sequence_dimension_index],)
        iteration_start_index = xdata.sequence_dimension_index
        data_descriptor = DataAndMetadata.DataDescriptor(False, 0, 2)
    assert iteration_start_index is not None

    tableau_height = int(numpy.sqrt(numpy.prod(iteration_shape, dtype=numpy.int64)))
    tableau_width = int(numpy.ceil(numpy.prod(iteration_shape, dtype=numpy.int64) / tableau_height))
    tableau_shape = (tableau_height, tableau_width)

    result = typing.cast(None, numpy.ndarray)
    for i in range(numpy.prod(iteration_shape, dtype=numpy.int64)):
        coords = numpy.unravel_index(i, iteration_shape)
        data_coords = tuple([slice(None) for k in range(iteration_start_index)]) + coords
        if scale != 1.0:
            scale_sequence = [1.0] * iteration_start_index + [scale] * 2
            scaled_data = scipy.ndimage.zoom(xdata.data[data_coords], scale_sequence, order=1)
        else:
            scaled_data = xdata.data[data_coords]

        if i==0:
            result = numpy.zeros(xdata.data.shape[:iteration_start_index] + (scaled_data.shape[-2] * tableau_height, scaled_data.shape[-1] * tableau_width), dtype=xdata.data.dtype)

        coords = numpy.unravel_index(i, tableau_shape)
        pos = (coords[0] * scaled_data.shape[-2], coords[1] * scaled_data.shape[-1])
        result_coords = tuple([slice(None) for k in range(iteration_start_index)]) + (slice(pos[0], pos[0] + scaled_data.shape[-2]), slice(pos[1], pos[1] + scaled_data.shape[-1]))
        result[result_coords] = scaled_data

    dimensional_calibrations = list(copy.deepcopy(xdata.dimensional_calibrations))
    [dimensional_calibrations.pop(iteration_start_index) for _ in range(len(iteration_shape))]
    return DataAndMetadata.new_data_and_metadata(result,
                                                 intensity_calibration=xdata.intensity_calibration,
                                                 dimensional_calibrations=dimensional_calibrations,
                                                 metadata=xdata.metadata,
                                                 data_descriptor=data_descriptor)


class MeasureShifts(Symbolic.ComputationHandlerLike):
    label = _("Measure shifts")
    inputs = {"input_data_item": {"label": _("Input data item")},
              "axes_description": {"label": _("Measure shift along this axis")},
              "reference_index": {"label": _("Reference index for shifts")},
              "relative_shifts": {"label": _("Measure shifts relative to previous slice")},
              "max_shift": {"label": _("Max shift between consecutive frames (in pixels, <= 0 to disable)")},
              "bounds_graphic": {"label": _("Shift bounds")},
              }
    outputs = {"shifts": {"label": _("Shifts")},
               }

    def __init__(self, computation, **kwargs):
        self.computation = computation

    @staticmethod
    def guess_starting_axis(data_item: DataItem.DataItem, graphic: typing.Optional[API.Graphic] = None) -> str:
        # If we have a bound graphic we probably want to align the displayed dimensions
        if graphic:
            # For collections with 1D data we see the collection dimensions
            if data_item.xdata.is_collection and data_item.xdata.datum_dimension_count == 1:
                shift_axis = 'collection'
            # Otherwise we see the data dimensions
            else:
                shift_axis = 'data'
        # If not, use some generic rules
        else:
            shift_axis = 'data'

            if data_item.xdata.is_collection and data_item.xdata.datum_dimension_count == 1:
                shift_axis = 'collection'

        return shift_axis

    def execute(self, input_data_item: API.DataItem, axes_description: str, reference_index: typing.Union[None, int, typing.Sequence[int]]=None, relative_shifts: bool=True, max_shift: int=0, bounds_graphic: typing.Optional[API.Graphic]=None):
        input_xdata = input_data_item.xdata
        bounds = None
        if bounds_graphic is not None:
            if bounds_graphic.graphic_type == "interval-graphic":
                bounds = bounds_graphic.interval
            else:
                bounds = bounds_graphic.bounds
        split_description = axes_description.split("-")
        shift_axis = split_description[0]
        max_shift_ = max_shift if max_shift > 0 else None
        reference_index = reference_index if not relative_shifts else None
        self.__shifts_xdata = function_measure_multi_dimensional_shifts(input_xdata, shift_axis, reference_index=reference_index, bounds=bounds, max_shift=max_shift_)

    def commit(self):
        self.computation.set_referenced_xdata("shifts", self.__shifts_xdata)


class MeasureShiftsMenuItemDelegate:
    def __init__(self, api: API.API):
        self.__api = api
        self.menu_id = "multi_dimensional_processing_menu"
        self.menu_name = _("Multi-Dimensional Processing")
        self.menu_before_id = "window_menu"
        self.menu_item_name = _("Measure shifts")

    def menu_item_execute(self, window: API.DocumentWindow):
        selected_data_item = window.target_data_item

        if not selected_data_item or not selected_data_item.xdata:
            return

        bounds_graphic = None
        if selected_data_item.display.selected_graphics:
            for graphic in selected_data_item.display.selected_graphics:
                if graphic.graphic_type in {"rect-graphic", "interval-graphic"}:
                    bounds_graphic = graphic

        shift_axis = MeasureShifts.guess_starting_axis(selected_data_item, graphic=bounds_graphic)

        # Make a result data item with 3 dimensions to ensure we get a large_format data item
        result_data_item = self.__api.library.create_data_item_from_data(numpy.zeros((1,1,1)), title="Shifts of {}".format(selected_data_item.title))

        # shift_axis_structure = DataStructure.DataStructure(structure_type=shift_axis)
        # self.__api.library._document_model.append_data_structure(shift_axis_structure)
        # shift_axis_structure.source = result_data_item._data_item

        inputs = {"input_data_item": {"object": selected_data_item, "type": "data_source"},
                  "axes_description": shift_axis,
                  "reference_index": 0,
                  "relative_shifts": True,
                  "max_shift": 0,
                  }
        if bounds_graphic:
            inputs["bounds_graphic"] = bounds_graphic

        self.__api.library.create_computation("nion.measure_shifts",
                                              inputs=inputs,
                                              outputs={"shifts": result_data_item})
        window.display_data_item(result_data_item)


class ApplyShifts(Symbolic.ComputationHandlerLike):
    label = _("Apply shifts")
    inputs = {"input_data_item": {"label": _("Input data item")},
              "shifts_data_item": {"label": _("Shifts data item")},
              "axes_description": {"label": _("Apply shift along this axis")},
              }
    outputs = {"shifted": {"label": _("Shifted")},
               }

    def __init__(self, computation, **kwargs):
        self.computation = computation

    @staticmethod
    def guess_starting_axis(shifts_xdata: DataAndMetadata.DataAndMetadata, input_xdata: DataAndMetadata.DataAndMetadata) -> str:
        shifts_shape = shifts_xdata.data.shape
        data_shape = input_xdata.data.shape
        for i in range(len(data_shape) - len(shifts_shape) + 1):
            if data_shape[i:i+len(shifts_shape)] == shifts_shape:
                shifts_start_axis = i
                shifts_end_axis = i + len(shifts_shape)
                break
            elif data_shape[i:i+len(shifts_shape)-1] == shifts_shape[:-1] and shifts_shape[-1] == 2:
                shifts_start_axis = i
                shifts_end_axis = i + len(shifts_shape) - 1
                break
        else:
            raise ValueError("Did not find any axis matching the shifts shape.")

        shifts_indexes = range(shifts_start_axis, shifts_end_axis)
        shift_axis_points = {"collection": 0, "sequence": 0, "data": 0}
        if input_xdata.is_collection:
            collection_dimension_indexes = input_xdata.collection_dimension_indexes
            cond = False
            for ind in collection_dimension_indexes:
                if ind in shifts_indexes:
                    cond = True
            if not cond and (len(collection_dimension_indexes) == 1 or len(collection_dimension_indexes) == shifts_shape[-1]):
                shift_axis_points["collection"] += 1

        if input_xdata.is_sequence:
            sequence_dimension_index = input_xdata.sequence_dimension_index
            if not sequence_dimension_index in shifts_indexes:
                shift_axis_points["sequence"] += 1

        datum_dimension_indexes = input_xdata.datum_dimension_indexes
        cond = False
        for ind in datum_dimension_indexes:
            if ind in shifts_indexes:
                cond = True
        if not cond and (len(datum_dimension_indexes) == 1 or len(datum_dimension_indexes) == shifts_shape[-1]):
            shift_axis_points["data"] += 1

        if shift_axis_points["collection"] > 0:
            shift_axis = "collection"
        elif shift_axis_points["data"] > 0:
            shift_axis = "data"
        elif shift_axis_points["sequence"] > 0:
            shift_axis = "sequence"
        else:
            shift_axis = "data"

        return shift_axis

    def execute(self, input_data_item: API.DataItem, shifts_data_item: API.DataItem, axes_description: str):
        input_xdata = input_data_item.xdata
        shifts  = shifts_data_item.data
        split_description = axes_description.split("-")
        shift_axis = split_description[0]
        # Like this we directly write to the underlying storage and don't have to cache everything in memory first
        result_data_item = self.computation.get_result('shifted')
        function_apply_multi_dimensional_shifts(input_xdata, shifts, shift_axis, out=result_data_item.xdata)
        # self.__result_xdata = function_apply_multi_dimensional_shifts(input_xdata, shifts, shift_axis)

    def commit(self):
        # self.computation.set_referenced_xdata("shifted", self.__result_xdata)
        # self.__result_xdata = None
        # Still call "set_referenced_xdata" to notify Swift that the data has been updated.
        self.computation.set_referenced_xdata("shifted", self.computation.get_result("shifted").xdata)


class ApplyShiftsMenuItemDelegate:
    def __init__(self, api: API.API):
        self.__api = api
        self.menu_id = "multi_dimensional_processing_menu"
        self.menu_name = _("Multi-Dimensional Processing")
        self.menu_before_id = "window_menu"
        self.menu_item_name = _("Apply shifts")

    def menu_item_execute(self, window: API.DocumentWindow):
        selected_display_items = window._document_controller._get_two_data_sources()
        error_msg = "Select a multi-dimensional data item and another one that contains shifts that can be broadcast to the shape of the first one."
        assert selected_display_items[0][0] is not None, error_msg
        assert selected_display_items[1][0] is not None, error_msg
        assert selected_display_items[0][0].data_item is not None, error_msg
        assert selected_display_items[1][0].data_item is not None, error_msg
        assert selected_display_items[0][0].data_item.xdata is not None, error_msg
        assert selected_display_items[1][0].data_item.xdata is not None, error_msg

        di_1 = selected_display_items[0][0].data_item
        di_2 = selected_display_items[1][0].data_item

        if len(di_1.data.shape) < len(di_2.data.shape):
            shifts_di = self.__api._new_api_object(di_1)
            input_di = self.__api._new_api_object(di_2)
        elif len(di_2.data.shape) < len(di_1.data.shape):
            shifts_di = self.__api._new_api_object(di_2)
            input_di = self.__api._new_api_object(di_1)
        else:
            raise ValueError(error_msg)

        shift_axis = ApplyShifts.guess_starting_axis(shifts_di.xdata, input_di.xdata)

        data_item = DataItem.DataItem(large_format=True)
        data_item.title="Shifted {}".format(input_di.title)
        window._document_controller.document_model.append_data_item(data_item)
        data_item.reserve_data(data_shape=input_di.xdata.data_shape, data_dtype=input_di.xdata.data_dtype, data_descriptor=input_di.xdata.data_descriptor)
        data_item.dimensional_calibrations = input_di.xdata.dimensional_calibrations
        data_item.intensity_calibration = input_di.xdata.intensity_calibration
        data_item.metadata = copy.deepcopy(input_di.xdata.metadata)
        result_data_item = self.__api._new_api_object(data_item)

        # shift_axis_structure = DataStructure.DataStructure(structure_type=shift_axis)
        # self.__api.library._document_model.append_data_structure(shift_axis_structure)
        # shift_axis_structure.source = result_data_item._data_item

        inputs = {"input_data_item": {"object": input_di, "type": "data_source"},
                  "shifts_data_item": {"object": shifts_di, "type": "data_source"},
                  "axes_description": shift_axis
                  }

        self.__api.library.create_computation("nion.apply_shifts",
                                              inputs=inputs,
                                              outputs={"shifted": result_data_item})
        window.display_data_item(result_data_item)


class IntegrateAlongAxisMenuItemDelegate:
    def __init__(self, api: API.API):
        self.__api = api
        self.menu_id = "multi_dimensional_processing_menu"
        self.menu_name = _("Multi-Dimensional Processing")
        self.menu_before_id = "window_menu"
        self.menu_item_name = _("Integrate axis")

    def menu_item_execute(self, window: API.DocumentWindow):
        selected_data_item = window.target_data_item

        if not selected_data_item or not selected_data_item.xdata:
            return

        integrate_graphic = None
        if selected_data_item.display.selected_graphics:
            integrate_graphic = selected_data_item.display.selected_graphics[0]

        integration_axes = IntegrateAlongAxis.guess_starting_axis(selected_data_item, graphic=integrate_graphic)

        # Make a result data item with 3 dimensions to ensure we get a large_format data item
        result_data_item = self.__api.library.create_data_item_from_data(numpy.zeros((1,1,1)), title="Integrated {}".format(selected_data_item.title))

        # integration_axes_structure = DataStructure.DataStructure(structure_type=integration_axes)
        # self.__api.library._document_model.append_data_structure(integration_axes_structure)
        # integration_axes_structure.source = result_data_item._data_item
        #
        # integration_sub_axes_structure = DataStructure.DataStructure(structure_type="all")
        # self.__api.library._document_model.append_data_structure(integration_sub_axes_structure)
        # integration_sub_axes_structure.source = result_data_item._data_item

        inputs = {"input_data_item": {"object": selected_data_item, "type": "data_source"},
                  "axes_description": integration_axes + "-all"
                  # "integration_axes": self.__api._new_api_object(integration_axes_structure),
                  # "sub_integration_axes": self.__api._new_api_object(integration_sub_axes_structure),
                  }
        if integrate_graphic:
            inputs["integration_graphic"] = integrate_graphic

        self.__api.library.create_computation("nion.integrate_along_axis",
                                              inputs=inputs,
                                              outputs={"integrated": result_data_item})
        window.display_data_item(result_data_item)


class Crop:
    label = _("Crop")
    inputs = {"input_data_item": {"label": _("Input data item")},
              "axes_description": {"label": _("Crop along this axis")},
              "crop_graphic": {"label": _("Crop bounds")},
              "crop_bounds_left": {"label": _("Crop bound left")},
              "crop_bounds_right": {"label": _("Crop bound right")},
              "crop_bounds_top": {"label": _("Crop bound top")},
              "crop_bounds_bottom": {"label": _("Crop bound bottom")}}
    outputs = {"cropped": {"label": _("Cropped")}}

    def __init__(self, computation, **kwargs):
        self.computation = computation

    @staticmethod
    def guess_starting_axis(data_item, graphic) -> str:
        # If we have a crop graphic we probably want to crop the displayed dimensions
        if graphic:
            # For collections with 1D data we see the collection dimensions
            if data_item.xdata.is_collection and data_item.xdata.datum_dimension_count == 1:
                crop_axes = "collection"
            # Otherwise we see the data dimensions
            else:
                crop_axes = "data"
        # If not, use some generic rules
        else:
            if data_item.xdata.is_collection and data_item.xdata.datum_dimension_count == 1:
                crop_axes = "collection"
            else:
                crop_axes = "data"

        return crop_axes

    def execute(self, input_data_item: API.DataItem, axes_description: str, crop_graphic: typing.Optional[API.Graphic]=None, **kwargs):
        input_xdata: DataAndMetadata.DataAndMetadata = input_data_item.xdata
        crop_axis = axes_description
        if crop_axis == "collection":
            assert input_xdata.is_collection
            crop_axis_indices = list(input_xdata.collection_dimension_indexes)
        elif crop_axis == "sequence":
            assert input_xdata.is_sequence
            crop_axis_indices = [input_xdata.sequence_dimension_index]
        else:
            crop_axis_indices = list(input_xdata.datum_dimension_indexes)

        if crop_graphic is not None:
            if len(crop_axis_indices) == 2:
                bounds = crop_graphic.bounds
                assert numpy.ndim(bounds) == 2
                crop_bounds_left = bounds[0][1] * input_xdata.data_shape[crop_axis_indices[1]]
                crop_bounds_right = (bounds[0][1] + bounds[1][1]) * input_xdata.data_shape[crop_axis_indices[1]]
                crop_bounds_top = bounds[0][0] * input_xdata.data_shape[crop_axis_indices[0]]
                crop_bounds_bottom = (bounds[0][0] + bounds[1][0]) * input_xdata.data_shape[crop_axis_indices[0]]
            else:
                bounds = crop_graphic.interval
                assert numpy.ndim(bounds) == 1
                crop_bounds_left = bounds[0] * input_xdata.data_shape[crop_axis_indices[0]]
                crop_bounds_right = bounds[1] * input_xdata.data_shape[crop_axis_indices[0]]
        else:
            crop_bounds_left = kwargs.get("crop_bounds_left")
            crop_bounds_right = kwargs.get("crop_bounds_right")
            crop_bounds_top = kwargs.get("crop_bounds_top")
            crop_bounds_bottom = kwargs.get("crop_bounds_bottom")

        if len(crop_axis_indices) == 2:
            crop_bounds_left = int(crop_bounds_left)
            crop_bounds_right = int(crop_bounds_right)
            crop_bounds_top = int(crop_bounds_top)
            crop_bounds_bottom = int(crop_bounds_bottom)
            crop_bounds_left = max(0, crop_bounds_left)
            crop_bounds_top = max(0, crop_bounds_top)
            if crop_bounds_right == -1:
                crop_bounds_right = None
            else:
                crop_bounds_right = min(crop_bounds_right, input_xdata.data_shape[crop_axis_indices[1]])
            if crop_bounds_bottom == -1:
                crop_bounds_bottom = None
            else:
                crop_bounds_bottom = min(crop_bounds_bottom, input_xdata.data_shape[crop_axis_indices[0]])
        else:
            crop_bounds_left = int(crop_bounds_left)
            crop_bounds_right = int(crop_bounds_right)
            crop_bounds_left = max(0, crop_bounds_left)
            if crop_bounds_right == -1:
                crop_bounds_right = None
            else:
                crop_bounds_right = min(crop_bounds_right, input_xdata.data_shape[crop_axis_indices[0]])

        crop_slices: typing.Tuple[slice, ...] = tuple()
        for i in range(len(input_xdata.data_shape)):
            if len(crop_axis_indices) == 1 and i == crop_axis_indices[0]:
                crop_slices += (slice(crop_bounds_left, crop_bounds_right),)
            elif len(crop_axis_indices) == 2 and i == crop_axis_indices[0]:
                crop_slices += (slice(crop_bounds_top, crop_bounds_bottom),)
            elif len(crop_axis_indices) == 2 and i == crop_axis_indices[1]:
                crop_slices += (slice(crop_bounds_left, crop_bounds_right),)
            else:
                crop_slices += (slice(None),)

        self.__result_xdata = input_xdata[crop_slices]

    def commit(self):
        self.computation.set_referenced_xdata("cropped", self.__result_xdata)


class CropMenuItemDelegate:
    def __init__(self, api: API.API):
        self.__api = api
        self.menu_id = "multi_dimensional_processing_menu"
        self.menu_name = _("Multi-Dimensional Processing")
        self.menu_before_id = "window_menu"
        self.menu_item_name = _("Crop")

    def menu_item_execute(self, window: API.DocumentWindow):
        selected_data_item = window.target_data_item

        if not selected_data_item or not selected_data_item.xdata:
            return

        crop_graphic = None
        if selected_data_item.display.selected_graphics:
            for graphic in selected_data_item.display.selected_graphics:
                if graphic.graphic_type in {"rect-graphic", "interval-graphic"}:
                    crop_graphic = graphic
                    break

        crop_axes = Crop.guess_starting_axis(selected_data_item, crop_graphic)

        # Make a result data item with 3 dimensions to ensure we get a large_format data item
        result_data_item = self.__api.library.create_data_item_from_data(numpy.zeros((1,1,1)), title="Cropped {}".format(selected_data_item.title))

        # crop_axes_structure = DataStructure.DataStructure(structure_type=crop_axes)
        # self.__api.library._document_model.append_data_structure(crop_axes_structure)
        # crop_axes_structure.source = result_data_item._data_item

        inputs = {"input_data_item": {"object": selected_data_item, "type": "data_source"},
                  "axes_description": crop_axes
                  }
        if crop_graphic:
            inputs["crop_graphic"] = crop_graphic
        else:
            inputs["crop_bounds_left"] = 0
            inputs["crop_bounds_right"] = -1
            inputs["crop_bounds_top"] = 0
            inputs["crop_bounds_bottom"] = -1

        self.__api.library.create_computation("nion.crop_multi_dimensional",
                                              inputs=inputs,
                                              outputs={"cropped": result_data_item})
        window.display_data_item(result_data_item)


class MakeTableau:
    label = _("Display Tableau")
    inputs = {"input_data_item": {"label": _("Input data item")},
              "scale": {"label": _("Scale")}}
    outputs = {"tableau": {"label": "Tableau"}}

    def __init__(self, computation, **kwargs):
        self.computation = computation

    def execute(self, input_data_item: API.DataItem, scale: float):
        try:
            self.__result_xdata = function_make_tableau_image(input_data_item.xdata, scale)
        except:
            import traceback
            traceback.print_exc()
            raise

    def commit(self):
        self.computation.set_referenced_xdata("tableau", self.__result_xdata)
        self.__result_xdata = None


class MakeTableauMenuItemDelegate:
    def __init__(self, api: API.API):
        self.__api = api
        self.menu_id = "multi_dimensional_processing_menu"
        self.menu_name = _("Multi-Dimensional Processing")
        self.menu_before_id = "window_menu"
        self.menu_item_name = _("Make tableau image")

    def menu_item_execute(self, window: API.DocumentWindow):
        selected_data_item = window.target_data_item
        error_msg = "Select one data item that contains a sequence or collection of two-dimensional data."
        assert selected_data_item is not None, error_msg
        assert selected_data_item.xdata is not None, error_msg
        assert selected_data_item.xdata.is_sequence or selected_data_item.xdata.is_collection, error_msg
        assert selected_data_item.xdata.datum_dimension_count == 2, error_msg

        # Limit the maximum size of the result to something sensible:
        max_result_pixels = 8192
        if selected_data_item.xdata.is_collection:
            scale = min(1.0, max_result_pixels / (numpy.sqrt(numpy.prod(selected_data_item.xdata.collection_dimension_shape)) *
                                                  numpy.sqrt(numpy.prod(selected_data_item.xdata.datum_dimension_shape))))
        elif selected_data_item.xdata.is_sequence:
            scale = min(1.0, max_result_pixels / (numpy.sqrt(numpy.prod(selected_data_item.xdata.sequence_dimension_shape)) *
                                                  numpy.sqrt(numpy.prod(selected_data_item.xdata.datum_dimension_shape))))

        inputs = {"input_data_item": {"object": selected_data_item, "type": "data_source"},
                  "scale": scale}

        # Make a result data item with 3 dimensions to ensure we get a large_format data item
        result_data_item = self.__api.library.create_data_item_from_data(numpy.zeros((1,1,1)), title="Tableau of {}".format(selected_data_item.title))

        self.__api.library.create_computation("nion.make_tableau_image",
                                              inputs=inputs,
                                              outputs={"tableau": result_data_item})

        window.display_data_item(result_data_item)


class AlignImageSequence(Symbolic.ComputationHandlerLike):
    label = _("Align and integrate image sequence")
    inputs = {"input_data_item": {"label": _("Input data item")},
              "reference_index": {"label": _("Reference index for shifts")},
              "relative_shifts": {"label": _("Measure shifts relative to previous slice")},
              "max_shift": {"label": _("Max shift between consecutive frames (in pixels, <= 0 to disable)")},
              "bounds_graphic": {"label": _("Shift bounds")},
              }
    outputs = {"shifts": {"label": _("Shifts")},
               "integrated_sequence": {"label": _("Integrated sequence")},
               }

    def __init__(self, computation, **kwargs):
        self.computation = computation

    def execute(self, *, input_data_item: API.DataItem, reference_index: typing.Union[None, int, typing.Sequence[int]]=None, relative_shifts: bool=True, max_shift: int=0, bounds_graphic: typing.Optional[API.Graphic]=None):
        input_xdata = input_data_item.xdata
        bounds = None
        if bounds_graphic is not None:
            bounds = bounds_graphic.bounds
        max_shift_ = max_shift if max_shift > 0 else None
        reference_index = reference_index if not relative_shifts else None
        shifts_xdata = function_measure_multi_dimensional_shifts(input_xdata, 'data', reference_index=reference_index, bounds=bounds, max_shift=max_shift_)
        self.__shifts_xdata = Core.function_transpose_flip(shifts_xdata, transpose=True, flip_v=False, flip_h=False)
        aligned_input_xdata = function_apply_multi_dimensional_shifts(input_xdata, shifts_xdata.data, 'data')
        self.__integrated_input_xdata = Core.function_sum(aligned_input_xdata, axis=0)

    def commit(self):
        self.computation.set_referenced_xdata("shifts", self.__shifts_xdata)
        self.computation.set_referenced_xdata("integrated_sequence", self.__integrated_input_xdata)


class AlignImageSequenceMenuItemDelegate:

    def __init__(self, api):
        self.__api = api
        self.menu_id = "processing_menu"  # required, specify menu_id where this item will go
        self.menu_name = _("Processing")  # optional, specify default name if not a standard menu
        self.menu_before_id = "window_menu"  # optional, specify before menu_id if not a standard menu
        self.menu_item_name = _("[EXPERIMENTAL] Align image sequence")  # menu item name

    def menu_item_execute(self, window: API.DocumentWindow):
        try:
            selected_data_item = window.target_data_item
            error_msg = "Select one data item that contains a sequence or 1D-collection of two-dimensional data."
            assert selected_data_item is not None, error_msg
            assert selected_data_item.xdata is not None, error_msg
            assert selected_data_item.xdata.is_sequence or selected_data_item.xdata.is_collection, error_msg
            assert not (selected_data_item.xdata.is_sequence and selected_data_item.xdata.is_collection), error_msg
            if selected_data_item.xdata.is_collection:
                assert selected_data_item.xdata.collection_dimension_count == 1, error_msg
            assert selected_data_item.xdata.datum_dimension_count == 2, error_msg

            bounds_graphic = None
            if selected_data_item.display.selected_graphics:
                for graphic in selected_data_item.display.selected_graphics:
                    if graphic.graphic_type in {"rect-graphic", "interval-graphic"}:
                        bounds_graphic = graphic

            # Make a result data item with 3 dimensions to ensure we get a large_format data item
            result_data_item = self.__api.library.create_data_item_from_data(numpy.zeros((1,1,1)), title=f"{selected_data_item.title} aligned and integrated")
            shifts = self.__api.library.create_data_item_from_data(numpy.zeros((2, 2)), title=f"{selected_data_item.title} measured shifts")

            inputs = {"input_data_item": {"object": selected_data_item, "type": "data_source"},
                      "reference_index": 0,
                      "relative_shifts": False,
                      "max_shift": 0,
                      }
            if bounds_graphic:
                inputs["bounds_graphic"] = bounds_graphic

            self.__api.library.create_computation("nion.align_and_integrate_image_sequence",
                                                  inputs=inputs,
                                                  outputs={"shifts": shifts,
                                                           "integrated_sequence": result_data_item})
            window.display_data_item(result_data_item)
            window.display_data_item(shifts)

            display_item = self.__api.library._document_model.get_display_item_for_data_item(shifts._data_item)
            display_item.display_type = "line_plot"
            display_item._set_display_layer_properties(0, stroke_color='#1E90FF', stroke_width=2, fill_color=None, label="y")
            display_item._set_display_layer_properties(1, stroke_color='#F00', stroke_width=2, fill_color=None, label="x")

        except Exception as e:
            import traceback
            traceback.print_exc()
            from nion.swift.model import Notification
            Notification.notify(Notification.Notification("nion.computation.error", "\N{WARNING SIGN} Computation", "Align sequence of images failed", str(e)))


class MultiDimensionalProcessingExtension:

    extension_id = "nion.experimental.multi_dimensional_processing"

    def __init__(self, api_broker):
        api = api_broker.get_api(version="~1.0")
        self.__integrate_menu_item_ref = api.create_menu_item(IntegrateAlongAxisMenuItemDelegate(api))
        self.__measure_shifts_menu_item_ref = api.create_menu_item(MeasureShiftsMenuItemDelegate(api))
        self.__apply_shifts_menu_item_ref = api.create_menu_item(ApplyShiftsMenuItemDelegate(api))
        self.__crop_menu_item_ref = api.create_menu_item(CropMenuItemDelegate(api))
        self.__tableau_menu_item_ref = api.create_menu_item(MakeTableauMenuItemDelegate(api))
        self.__align_image_sequence_menu_item_ref = api.create_menu_item(AlignImageSequenceMenuItemDelegate(api))

    def close(self):
        self.__integrate_menu_item_ref.close()
        self.__integrate_menu_item_ref = None
        self.__measure_shifts_menu_item_ref.close()
        self.__measure_shifts_menu_item_ref = None
        self.__apply_shifts_menu_item_ref.close()
        self.__apply_shifts_menu_item_ref = None
        self.__crop_menu_item_ref.close()
        self.__crop_menu_item_ref = None
        self.__tableau_menu_item_ref.close()
        self.__tableau_menu_item_ref = None
        self.__align_image_sequence_menu_item_ref.close()
        self.__align_image_sequence_menu_item_ref = None


class AxisChoiceVariableHandler(Observable.Observable):
    def __init__(self, computation: Symbolic.Computation, computation_variable: Symbolic.ComputationVariable, variable_model: Inspector.VariableValueModel, sub_axes_enabled: bool):
        super().__init__()
        self.computation = computation
        self.computation_variable = computation_variable
        self.variable_model = variable_model
        self.sub_axes_enabled = sub_axes_enabled

        self.__axes_index = 0
        self.__sub_axes_visible = False
        self.__sub_axes_index = 0

        self.initialize()

        u = Declarative.DeclarativeUI()
        label = u.create_label(text="@binding(computation_variable.display_label)")
        axes_combo_box = u.create_combo_box(items_ref="@binding(axes)", current_index="@binding(axes_index)")
        sub_axes_combo_box = u.create_combo_box(items_ref="@binding(sub_axes)", current_index="@binding(sub_axes_index)", visible="@binding(sub_axes_visible)")
        self.ui_view = u.create_column(label, u.create_row(axes_combo_box, sub_axes_combo_box, u.create_stretch(), spacing=8))

        def handle_item_inserted(*args, **kwargs):
            self.property_changed_event.fire("axes")
            self.property_changed_event.fire("sub_axes")
            input_data_item = self.computation.get_input("input_data_item")
            new_value = None
            if self.computation.processing_id == "nion.apply_shifts":
                shifts_data_item = self.computation.get_input("shifts_data_item")
                if input_data_item and shifts_data_item:
                    compute_class = Symbolic._computation_types.get(self.computation.processing_id)
                    if compute_class:
                        new_value = compute_class.guess_starting_axis(input_data_item, shifts_data_item)
            else:
                if input_data_item:
                    compute_class = Symbolic._computation_types.get(self.computation.processing_id)
                    if compute_class:
                        new_value = compute_class.guess_starting_axis(input_data_item)
            if new_value is not None:
                self.variable_model.value = new_value
            self.initialize()

        self.__item_inserted_listener = self.computation.item_inserted_event.listen(handle_item_inserted)

    def initialize(self):
        axes_description = self.variable_model.value
        split_description = axes_description.split("-")
        self.axes_index = self.__get_available_axis_choices().index(split_description[0])
        choices = self.__get_available_sub_axis_choices(self.current_axis)
        self.sub_axes_visible = bool(choices)
        if choices and len(split_description) > 1:
            self.sub_axes_index = choices.index(split_description[1])

    def close(self):
        self.__item_inserted_listener = typing.cast(typing.Any, None)

    def update(self):
        current_axis = self.current_axis
        current_sub_axis = self.current_sub_axis
        self.sub_axes_visible = bool(current_sub_axis)
        axes_description = ""
        if current_axis:
            axes_description += current_axis
            if current_sub_axis:
                axes_description += "-" + current_sub_axis
        self.variable_model.value = axes_description
        self.property_changed_event.fire("sub_axes")

    @property
    def __axes_labels(self):
        return {"sequence": _("Sequence"),
                "collection": _("Collection"),
                "data": _("Data")}

    @property
    def __sub_axes_labels(self):
        return {"first": _("First"),
                "second": _("Second"),
                "all": _("All")}

    def __get_available_axis_choices(self) -> typing.List[str]:
        axis_choices = []
        input_data_item = self.computation.get_input("input_data_item")
        if input_data_item and input_data_item.xdata:
            if input_data_item.xdata.is_sequence:
                axis_choices.append("sequence")
            if input_data_item.xdata.is_collection:
                axis_choices.append("collection")
            axis_choices.append("data")
        return axis_choices

    def __get_available_sub_axis_choices(self, axis: str) -> typing.List[str]:
        sub_axis_choices = []
        input_data_item = self.computation.get_input("input_data_item")
        if axis and input_data_item and input_data_item.xdata:
            dimension_count = 0
            if axis == "collection":
                dimension_count = input_data_item.xdata.collection_dimension_count
            elif axis == "data":
                dimension_count = input_data_item.xdata.datum_dimension_count
            if dimension_count > 1:
                sub_axis_choices = ["all", "first", "second"]
        return sub_axis_choices

    @property
    def current_axis(self) -> typing.Optional[str]:
        choices = self.__get_available_axis_choices()
        if choices:
            return choices[min(self.axes_index, len(choices) - 1)]

    @property
    def current_sub_axis(self) -> typing.Optional[str]:
        choices = self.__get_available_sub_axis_choices(self.current_axis)
        if choices:
            return choices[min(self.sub_axes_index, len(choices) - 1)]

    @property
    def axes(self) -> typing.List[str]:
        return [self.__axes_labels[entry] for entry in self.__get_available_axis_choices()]

    @axes.setter
    def axes(self, axes: typing.List[str]):
        ...

    @property
    def sub_axes(self) -> typing.List[str]:
        return self.__get_available_sub_axis_choices(self.current_axis)

    @sub_axes.setter
    def sub_axes(self, sub_axes: typing.List[str]):
        ...

    @property
    def axes_index(self) -> int:
        return self.__axes_index

    @axes_index.setter
    def axes_index(self, axes_index: int):
        self.__axes_index = axes_index
        self.update()
        # self.property_changed_event.fire("axes_index")

    @property
    def sub_axes_index(self) -> int:
        return self.__sub_axes_index

    @sub_axes_index.setter
    def sub_axes_index(self, sub_axes_index: int):
        self.__sub_axes_index = sub_axes_index
        self.update()
        # self.property_changed_event.fire("sub_axes_index")

    @property
    def sub_axes_visible(self) -> bool:
        return self.__sub_axes_visible

    @sub_axes_visible.setter
    def sub_axes_visible(self, visible: bool):
        self.__sub_axes_visible = visible if self.sub_axes_enabled else False
        self.property_changed_event.fire("sub_axes_visible")


class AxisChoiceVariableHandlerFactory(Inspector.VariableHandlerComponentFactory):
    def make_variable_handler(self, document_controller: DocumentController.DocumentController, computation: Symbolic.Computation, computation_variable: Symbolic.ComputationVariable, variable_model: Inspector.VariableValueModel) -> typing.Optional[Declarative.HandlerLike]:
        if computation.processing_id == "nion.integrate_along_axis" and computation_variable.name == "axes_description":
            return AxisChoiceVariableHandler(computation, computation_variable, variable_model, True)
        if computation.processing_id in {"nion.measure_shifts", "nion.apply_shifts", "nion.crop_multi_dimensional"} and computation_variable.name == "axes_description":
            return AxisChoiceVariableHandler(computation, computation_variable, variable_model, False)


Registry.register_component(AxisChoiceVariableHandlerFactory(), {"variable-handler-component-factory"})

Symbolic.register_computation_type("nion.integrate_along_axis", IntegrateAlongAxis)
Symbolic.register_computation_type("nion.measure_shifts", MeasureShifts)
Symbolic.register_computation_type("nion.apply_shifts", ApplyShifts)
Symbolic.register_computation_type("nion.crop_multi_dimensional", Crop)
Symbolic.register_computation_type("nion.make_tableau_image", MakeTableau)
Symbolic.register_computation_type("nion.align_and_integrate_image_sequence", AlignImageSequence)
