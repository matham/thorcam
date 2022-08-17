"""Thor camera internal interface
=================================

When run, it creates a server that accepts requests from a client, pass it
on to the camera, and then passes camera responses back to the client.

This module is intended to be run as a sub-process because running it in the
main process may cause incompatibility with other project, given that special
.NET binaries are loaded.

"""
from queue import Queue, Empty
import os
import select
import socket
from time import perf_counter as clock
import traceback
import sys
from threading import Thread
import numpy as np
import ctypes
import logging
from os.path import join
import struct
import ruamel.yaml
import warnings

import thorcam
from thorcam.camera import yaml_loads, yaml_dumps, connection_errors, \
    EndConnection, ThorCamBase

if os.environ.get('THORCAM_DOCS_GEN') != '1':
    import clr
    import System
    from System import Array, Int32, UInt16
    from System.Runtime.InteropServices import GCHandle, GCHandleType

__all__ = ('TSICamera', 'ThorCamServer')

warnings.simplefilter('ignore', ruamel.yaml.error.MantissaNoDotYAML1_1Warning)

_MAP_NET_NP = {
    'Single': np.dtype('float32'),
    'Double': np.dtype('float64'),
    'SByte': np.dtype('int8'),
    'Int16': np.dtype('int16'),
    'Int32': np.dtype('int32'),
    'Int64': np.dtype('int64'),
    'Byte': np.dtype('uint8'),
    'UInt16': np.dtype('uint16'),
    'UInt32': np.dtype('uint32'),
    'UInt64': np.dtype('uint64'),
    'Boolean': np.dtype('bool'),
}


def as_numpy_array(netArray):
    '''
    Given a CLR `System.Array` returns a `numpy.ndarray`.
    '''
    dims = np.empty(netArray.Rank, dtype=int)
    for I in range(netArray.Rank):
        dims[I] = netArray.GetLength(I)
    netType = netArray.GetType().GetElementType().Name

    try:
        npArray = np.empty(dims, order='C', dtype=_MAP_NET_NP[netType])
    except KeyError:
        raise NotImplementedError(
            "as_numpy_array does not yet support System type "
            "{}".format(netType))

    try:  # Memmove
        sourceHandle = GCHandle.Alloc(netArray, GCHandleType.Pinned)
        sourcePtr = sourceHandle.AddrOfPinnedObject().ToInt64()
        destPtr = npArray.__array_interface__['data'][0]
        ctypes.memmove(destPtr, sourcePtr, npArray.nbytes)
    finally:
        if sourceHandle.IsAllocated:
            sourceHandle.Free()
    return npArray


class TSICamera(ThorCamBase):
    """The interface to the Thor scientific camera.
    """

    playing = False
    """Whether the camera is currently playing.
    """

    tsi_sdk = None
    """The .NET sdk interface. See :attr:`ThorCamServer.tsi_sdk`
    """

    tsi_interface = None
    """The .NET interface. See :attr:`ThorCamServer.tsi_interface`
    """

    tsi_color_sdk = None
    """The Thor .NET ColorProcessorSDK interface object.
    """

    tsi_demosaicker = None
    """The Thor .NET Demosaicker module.
    """

    serial = ''
    """The serial of this camera.
    """

    to_cam_queue = None
    """The queue that the server uses to send requests to the camera.

    Client may send the following keys to the camera:
    `"close_cam"`, `"play"`, `"stop"`, or `"setting"`.

    See :class:`ThorCamServer` for details.
    """

    from_cam_queue = None
    """The queue that the camera uses to send requests to the server.

    The camera queue may send the following to the client:
    `exception`, `cam_closed`, `image`, `playing`, `cam_open`,
    `setting`, or `settings`.

    See :class:`ThorCamServer` for details.
    """

    camera_thread = None
    """Internal camera thread.
    """

    _freqs_to_str_map = {}

    _str_to_freqs_map = {}

    _taps_to_str_map = {}

    _str_to_taps_map = {}

    _color_processor = None

    _demosaic = None

    def __init__(self, tsi_sdk, tsi_interface, tsi_color_sdk,
                 tsi_demosaicker, serial):
        self.tsi_sdk = tsi_sdk
        self.tsi_interface = tsi_interface
        self.tsi_color_sdk = tsi_color_sdk
        self.tsi_demosaicker = tsi_demosaicker
        self.serial = serial
        self._freqs_to_str_map = {
            tsi_interface.DataRate.ReadoutSpeed20MHz: '20 MHz',
            tsi_interface.DataRate.ReadoutSpeed40MHz: '40 MHz',
            tsi_interface.DataRate.FPS50: '50 FPS',
            tsi_interface.DataRate.FPS30: '30 FPS',
        }
        self._str_to_freqs_map = {
            v: k for k, v in self._freqs_to_str_map.items()}
        self._taps_to_str_map = {
            tsi_interface.Taps.QuadTap: '4',
            tsi_interface.Taps.DualTap: '2',
            tsi_interface.Taps.SingleTap: '1',
        }
        self._str_to_taps_map = {
            v: k for k, v in self._taps_to_str_map.items()}

        to_cam_queue = self.to_cam_queue = Queue()
        from_cam_queue = self.from_cam_queue = Queue()
        thread = self.camera_thread = Thread(
            target=self.camera_run, args=(to_cam_queue, from_cam_queue))
        thread.start()

    def send_message(self, msg, value=None):
        """Send request to the camera.
        """
        if self.to_cam_queue:
            self.to_cam_queue.put((msg, value))

    def read_settings(self, cam):
        """Reads all the camera settings and returns it as a dict.
        """
        d = {}
        rang = cam.get_ExposureTimeRange_us()
        d['exposure_range'] = rang.Minimum / 1000., rang.Maximum / 1000.
        d['exposure_ms'] = cam.get_ExposureTime_us() / 1000.

        roi_bin = cam.get_ROIAndBin()

        rang = cam.get_BinXRange()
        d['binning_x_range'] = rang.Minimum, rang.Maximum
        d['binning_x'] = roi_bin.BinX

        rang = cam.get_BinYRange()
        d['binning_y_range'] = rang.Minimum, rang.Maximum
        d['binning_y'] = roi_bin.BinY

        d['sensor_size'] = [
            cam.get_SensorWidth_pixels(), cam.get_SensorHeight_pixels()]

        d['roi_x'] = roi_bin.ROIOriginX_pixels
        d['roi_y'] = roi_bin.ROIOriginY_pixels
        d['roi_width'] = roi_bin.ROIWidth_pixels
        d['roi_height'] = roi_bin.ROIHeight_pixels

        d['frame_queue_size'] = cam.get_MaximumNumberOfFramesToQueue()
        d['trigger_count'] = cam.get_FramesPerTrigger_zeroForUnlimited()
        hw_mode = cam.get_OperationMode() == \
            self.tsi_interface.OperationMode.HardwareTriggered
        d['trigger_type'] = self.supported_triggers[1 if hw_mode else 0]

        rang = cam.get_GainRange()
        d['gain_range'] = rang.Minimum, rang.Maximum
        if not d['gain_range'][1]:
            d['gain'] = 0
        else:
            d['gain'] = cam.get_Gain()

        rang = cam.get_BlackLevelRange()
        d['black_level_range'] = rang.Minimum, rang.Maximum
        if not d['black_level_range'][1]:
            d['black_level'] = 0
        else:
            d['black_level'] = cam.get_BlackLevel()

        freqs = []
        if cam.GetIsDataRateSupported(
                self.tsi_interface.DataRate.ReadoutSpeed20MHz):
            freqs.append('20 MHz')
        if cam.GetIsDataRateSupported(
                self.tsi_interface.DataRate.ReadoutSpeed40MHz):
            freqs.append('40 MHz')
        if cam.GetIsDataRateSupported(self.tsi_interface.DataRate.FPS30):
            freqs.append('30 FPS')
        if cam.GetIsDataRateSupported(self.tsi_interface.DataRate.FPS50):
            freqs.append('50 FPS')
        d['supported_freqs'] = freqs

        if freqs:
            d['freq'] = self._freqs_to_str_map[cam.get_DataRate()]

        if cam.GetIsTapsSupported(self.tsi_interface.Taps.QuadTap):
            d['supported_taps'] = ['1', '2', '4']
        elif cam.GetIsTapsSupported(self.tsi_interface.Taps.DualTap):
            d['supported_taps'] = ['1', '2']
        elif cam.GetIsTapsSupported(self.tsi_interface.Taps.SingleTap):
            d['supported_taps'] = ['1', ]
        else:
            d['supported_taps'] = []

        if d['supported_taps']:
            d['taps'] = self._taps_to_str_map[cam.get_Taps()]
        else:
            d['taps'] = ''

        d['supports_color'] = cam.get_CameraSensorType() == \
            self.tsi_interface.CameraSensorType.Bayer

        d['color_gain'] = self.color_gain

        for key, val in d.items():
            setattr(self, key, val)
        return d

    def write_setting(self, cam, setting, value):
        """Sets the camera setting and returns any changed settings as a dict.
        """
        values = {}
        if setting == 'exposure_ms':
            value = int(max(min(value, self.exposure_range[1]),
                        self.exposure_range[0]) * 1000)
            cam.set_ExposureTime_us(value)
            value = value / 1000.
        elif setting == 'binning_x':
            value = max(min(
                value, self.binning_x_range[1]), self.binning_x_range[0])
            roi_bin = cam.get_ROIAndBin()
            roi_bin.BinX = value
            cam.set_ROIAndBin(roi_bin)
        elif setting == 'binning_y':
            value = max(min(
                value, self.binning_y_range[1]), self.binning_y_range[0])
            roi_bin = cam.get_ROIAndBin()
            roi_bin.BinY = value
            cam.set_ROIAndBin(roi_bin)
        elif setting == 'roi_x':
            x = value = max(0, min(value, self.sensor_size[0] - 1))
            roi_bin = cam.get_ROIAndBin()
            width = min(self.sensor_size[0] - x, roi_bin.ROIWidth_pixels)

            roi_bin.ROIOriginX_pixels = x
            roi_bin.ROIWidth_pixels = width
            cam.set_ROIAndBin(roi_bin)
            values['roi_width'] = width
        elif setting == 'roi_y':
            y = value = max(0, min(value, self.sensor_size[1] - 1))
            roi_bin = cam.get_ROIAndBin()
            height = min(self.sensor_size[1] - y, roi_bin.ROIHeight_pixels)

            roi_bin.ROIOriginY_pixels = y
            roi_bin.ROIHeight_pixels = height
            cam.set_ROIAndBin(roi_bin)
            values['roi_height'] = height
        elif setting == 'roi_width':
            roi_bin = cam.get_ROIAndBin()
            x = roi_bin.ROIOriginX_pixels
            value = max(1, min(value, self.sensor_size[0] - x))
            roi_bin.ROIWidth_pixels = value
            cam.set_ROIAndBin(roi_bin)
            values['roi_x'] = x
        elif setting == 'roi_height':
            roi_bin = cam.get_ROIAndBin()
            y = roi_bin.ROIOriginY_pixels
            value = max(1, min(value, self.sensor_size[1] - y))
            roi_bin.ROIHeight_pixels = value
            cam.set_ROIAndBin(roi_bin)
            values['roi_y'] = y
        elif setting == 'trigger_type':
            hw_mode = value == self.supported_triggers[1]
            cam.set_OperationMode(
                self.tsi_interface.OperationMode.HardwareTriggered
                if hw_mode else
                self.tsi_interface.OperationMode.SoftwareTriggered)
        elif setting == 'trigger_count':
            cam.set_FramesPerTrigger_zeroForUnlimited(max(0, value))
        elif setting == 'frame_queue_size':
            cam.set_MaximumNumberOfFramesToQueue(max(1, value))
        elif setting == 'gain':
            value = int(
                max(min(value, self.gain_range[1]), self.gain_range[0]))
            cam.set_Gain(value)
        elif setting == 'black_level':
            value = int(max(
                min(value, self.black_level_range[1]),
                self.black_level_range[0]))
            cam.set_BlackLevel(value)
        elif setting == 'freq':
            cam.set_DataRate(self._str_to_freqs_map[value])
        elif setting == 'taps' and value:
            cam.set_Taps(self._str_to_taps_map[value])
        elif setting == 'color_gain':
            if self.supports_color:
                r, g, b = value
                mat = [r, 0, 0, 0, g, 0, 0, 0, b]

                self._color_processor.RemoveColorTransformMatrix(0)
                self._color_processor.InsertColorTransformMatrix(0, mat)
            else:
                value = [1, 1, 1]
        values[setting] = value

        for key, val in values.items():
            setattr(self, key, val)
        return values

    def read_frame(self, cam):
        """Reads a image from the camera, if available, and returns it.

        If available, it returns
        ``(data, fmt, (w, h), count, queued_count, t)``, otherwise, it returns
        None. See :class:`ThorCamServer`.
        """
        queued_count = cam.get_NumberOfQueuedFrames()
        if queued_count <= 0:
            return

        frame = cam.GetPendingFrameOrNull()
        t = clock()
        if not frame:
            return

        count = frame.FrameNumber
        h = frame.ImageData.Height_pixels
        w = frame.ImageData.Width_pixels
        frame_data = frame.ImageData.GetType().GetProperty(
            'ImageData_monoOrBGR').GetValue(frame.ImageData)
        if self._color_processor is not None:
            from Thorlabs.TSI import ColorInterfaces
            demosaicked_data = Array.CreateInstance(UInt16, h * w * 3)
            processed_data = Array.CreateInstance(UInt16, h * w * 3)
            fmt = ColorInterfaces.ColorFormat.BGRPixel
            max_pixel_val = int(2 ** cam.BitDepth - 1)

            self._demosaic.Demosaic(
                w, h, Int32(0), Int32(0), cam.ColorFilterArrayPhase,
                fmt, ColorInterfaces.ColorSensorType.Bayer,
                Int32(cam.BitDepth), frame_data,
                demosaicked_data)

            self._color_processor.Transform48To48(demosaicked_data, fmt,
                0, max_pixel_val, 0, max_pixel_val,
                0, max_pixel_val, 0, 0, 0, processed_data, fmt)

            pixel_fmt = 'bgr48le'
            data = as_numpy_array(processed_data)
        else:
            pixel_fmt = 'gray16le'
            data = as_numpy_array(frame_data)
        # img = Image(
        #     plane_buffers=[data.tobytes()],
        #     pix_fmt=pixel_fmt, size=(w, h))
        return data.tobytes(), pixel_fmt, (w, h), count, queued_count, t

    def verify_setting(self, playing, from_cam_queue, setting, value):
        """Checks whether the setting can be set currently, given the
        camera's state. Otherwise, it sends a error message to the server.
        """
        try:
            if playing:
                if setting not in self.play_settings:
                    raise ValueError(
                        'Setting "{}" cannot be set while the camera is '
                        'playing'.format(setting))
            else:
                if setting not in self.settings:
                    raise ValueError(
                        'Setting "{}" is not recognized'.format(setting))
        except Exception as e:
            exc_info = ''.join(
                traceback.format_exception(*sys.exc_info()))
            from_cam_queue.put(('exception', (str(e), exc_info)))
            return False
        return True

    def camera_run(self, to_cam_queue, from_cam_queue):
        """The thread that controls the camera.

        :param to_cam_queue: The ``Queue`` over which the server sends requests
            to the client.
        :param from_cam_queue: The ``Queue`` over which the client sends
            requests to the server.
        """
        verify = self.verify_setting
        cam = None
        playing = False
        msg = ''

        try:
            cam = self.tsi_sdk.OpenCamera(self.serial, False)
            settings = self.read_settings(cam)
            if self.supports_color:
                self._color_processor = self.tsi_color_sdk.\
                    CreateStandardRGBColorProcessor(
                        cam.GetDefaultWhiteBalanceMatrix(),
                        cam.GetCameraColorCorrectionMatrix(),
                        cam.BitDepth)
                mat = [1, 0, 0, 0, 1, 0, 0, 0, 1]
                self._color_processor.InsertColorTransformMatrix(0, mat)
                self._demosaic = self.tsi_demosaicker()

            from_cam_queue.put(('settings', settings))
            from_cam_queue.put(('cam_open', None))
            self.write_setting(cam, 'c_gain', (10, 0, 0))

            while msg != 'close_cam':
                if playing:
                    while True:
                        try:
                            msg, value = to_cam_queue.get(block=False)
                            if msg == 'close_cam':
                                break
                            elif msg == 'stop':
                                cam.Disarm()
                                playing = False
                                from_cam_queue.put(('playing', False))
                                break
                            elif msg == 'setting':
                                if verify(True, from_cam_queue, *value):
                                    from_cam_queue.put(
                                        ('setting',
                                         self.write_setting(cam, *value)))
                        except Empty:
                            break

                    if not playing or msg == 'close_cam':
                        continue
                    data = self.read_frame(cam)
                    if data is None:
                        continue

                    from_cam_queue.put(('image', data))

                else:
                    msg, value = to_cam_queue.get(block=True)
                    if msg == 'close_cam':
                        break
                    elif msg == 'play':
                        cam.Arm()
                        if self.trigger_type == self.supported_triggers[0]:
                            cam.IssueSoftwareTrigger()
                        playing = True
                        from_cam_queue.put(('playing', True))
                    elif msg == 'setting':
                        if verify(False, from_cam_queue, *value):
                            from_cam_queue.put(
                                ('setting', self.write_setting(cam, *value)))

        except Exception as e:
            exc_info = ''.join(traceback.format_exception(*sys.exc_info()))
            from_cam_queue.put(('exception', (str(e), exc_info)))

        finally:
            from_cam_queue.put(('cam_closed', None))
            try:
                if cam is not None:
                    if cam.IsArmed:
                        cam.Disarm()
                    cam.Dispose()
                if self._demosaic is not None:
                    self._demosaic.Dispose()
                if self._color_processor is not None:
                    self._color_processor.Dispose()
            except Exception:
                pass
        logging.info('TSICamera: exiting')


class ThorCamServer(object):
    """The class that runs the server and controls the camera.

    The following server messages are supported:

    :From client:

        `"close_cam"`:
            The camera should be closed and the object destroyed.
        `"play"`:
            Camera should start playing.
        `"stop"`:
            Camera should stop playing.
        `"setting"`: value is a tuple
            Sets a setting in the camera. During playing, only
            :attr:`thorcam.ThorCamBase.play_settings` may be set.
            The ``value`` is a tuple of setting name and its desired value.
        `"open_cam"`: Value is the serial
            That the camera should be opened. ``value`` is the serial number
            string of the camera to be opened.
        `"eof"`: None
            To close the server process and camera.
        `"serials"`: None
            Requests the list of cameras attached. It'll be sent back to the
            client.

    :To client:

        `exception`: tuple of string
            Camera had an exception. The ``value`` is a tuple of ``e`` and
            ``exc_info`` string.
        `cam_open`: None
            Sent when the camera has been successfully opened.
        `cam_closed`: None
            Camera closed and camera thread has exited.
        `image`: ``(data, fmt, (w, h), count, queued_count, t)``
            Sent when the camera has processed a new image. ``value`` is a
            tuple of image data and metadata.
            ``data`` is the ``bytes`` image data. ``fmt`` is the pixel format.
            ``(w, h)`` is the image size. ``count`` is the image number.
            ``queued_count`` is the number of frames the camera still has to
            process (i.e. need to be send from the hardware). ``t`` is the
            image timestamp.
        `playing`: bool
            Whether the camera is playing. ``value`` is a bool indicating
            whether it started stopped playing.
        `settings`: dict of ``setting: value``
            The settings value. A dict of settings and their values. Sent after
            camera is opened
        `setting`: dict of ``setting: value``
            The settings value. A dict of settings and their values. Send when
            a setting is updated.
        `"serials"`: List of serials
            Sends the list of cameras attached as list of serial number
            strings.
    """

    tsi_sdk = None
    """The Thor .NET sdk object.
    """

    tsi_interface = None
    """The Thor .NET interface object.
    """

    tsi_color_sdk = None
    """The Thor .NET ColorProcessorSDK interface object.
    """

    tsi_demosaicker = None
    """The Thor .NET Demosaicker module.
    """

    tsi_cam = None
    """The :class:`TSICamera`.
    """

    server = None
    """The server address to open. Set by the process when the class is
    instantiated. See :attr:`thorcam.camera.server`.
    """

    port = None
    """The server port to open. Set by the process when the class is
    instantiated. See :attr:`thorcam.camera.port`.
    """

    timeout = None
    """How long to wait wait on network requests, before checking the
    queues. Set by the process when the class is
    instantiated. See :attr:`thorcam.camera.timeout`.
    """

    thor_bin_path = None
    """The full path to where the Thor .NET binaries are located.
    Set by the process when the class is
    instantiated. See :attr:`thorcam.camera.thor_bin_path`.
    """

    server_thread = None
    """The server thread object.
    """

    def __init__(self, thor_bin_path, server, port, timeout):
        self.server = server
        self.port = int(port)
        self.timeout = float(timeout)

        if not thorcam.dep_bins:
            raise ValueError('Cannot find the thorcam .NET dlls')
        self.thor_bin_path = thor_bin_path

        self.load_tsi(thor_bin_path)

        thread = self.server_thread = Thread(target=self.server_run)
        thread.start()

    def load_tsi(self, thor_bin_path):
        """Loads the Thor .NET binaries and adds them to the PATH.
        """
        os.environ['PATH'] += os.pathsep + thor_bin_path
        clr.AddReference(
            join(thor_bin_path, 'Thorlabs.TSI.TLCamera.dll'))
        clr.AddReference(
            join(thor_bin_path, 'Thorlabs.TSI.TLCameraInterfaces.dll'))
        clr.AddReference(
            join(thor_bin_path, 'Thorlabs.TSI.Demosaicker.dll'))
        clr.AddReference(
            join(thor_bin_path, 'Thorlabs.TSI.ColorProcessor.dll'))
        from Thorlabs.TSI.TLCamera import TLCameraSDK
        import Thorlabs.TSI.TLCameraInterfaces as tsi_interface
        self.tsi_sdk = TLCameraSDK.OpenTLCameraSDK()
        self.tsi_interface = tsi_interface

        # Initialize the demosaicker
        from Thorlabs.TSI.Demosaicker import Demosaicker as demosaicker
        self.tsi_demosaicker = demosaicker
        from Thorlabs.TSI.ColorProcessor import ColorProcessorSDK
        self.tsi_color_sdk = ColorProcessorSDK()

    def get_tsi_cams(self):
        """Returns the list of serial numbers of the cameras attached."""
        cams = self.tsi_sdk.DiscoverAvailableCameras()
        return list(sorted(cams))

    def send_msg(self, sock, msg, value):
        """Sends message to the client."""
        bin_data = []
        if msg == 'image':
            buff, *value = value
            bin_data = [buff]

        data = yaml_dumps((msg, value))
        data = data.encode('utf8')

        sock.sendall(struct.pack('>II', len(data), sum(map(len, bin_data))))
        sock.sendall(data)
        for item in bin_data:
            sock.sendall(item)

    def decode_data(self, msg_buff, msg_len):
        """Decodes binary message from the client."""
        n, bin_n = msg_len
        assert not bin_n
        assert n == len(msg_buff)
        data = msg_buff.decode('utf8')
        msg, value = yaml_loads(data)

        return msg, value

    def read_msg(self, sock, msg_len, msg_buff):
        """Reads data from the client and decodes it."""
        # still reading msg size
        msg = value = None
        if not msg_len:
            assert 8 - len(msg_buff)
            data = sock.recv(8 - len(msg_buff))
            if not data:
                raise EndConnection('Remote client was closed')

            msg_buff += data
            if len(msg_buff) == 8:
                msg_len = struct.unpack('>II', msg_buff)
                msg_buff = b''
        else:
            total = sum(msg_len)
            assert total - len(msg_buff)
            data = sock.recv(total - len(msg_buff))
            if not data:
                raise EndConnection('Remote client was closed')

            msg_buff += data
            if len(msg_buff) == total:
                msg, value = self.decode_data(msg_buff, msg_len)

                msg_len = ()
                msg_buff = b''
        return msg_len, msg_buff, msg, value

    def process_client_message(self, sock, msg, value):
        """Handles a message received from the client.
        It responds or passes it on to the camera.
        """
        try:
            if msg in ('close_cam', 'play', 'stop', 'setting'):
                if self.tsi_cam is None:
                    raise TypeError('No camera has been opened')

                self.tsi_cam.send_message(msg, value)
            elif msg == 'open_cam':
                if self.tsi_cam is not None:
                    raise TypeError('Camera has already been opened')

                self.tsi_cam = TSICamera(
                    tsi_sdk=self.tsi_sdk, tsi_interface=self.tsi_interface,
                    tsi_color_sdk=self.tsi_color_sdk,
                    tsi_demosaicker=self.tsi_demosaicker, serial=value)
            elif msg == 'eof':
                return 'eof'
            elif msg == 'serials':
                self.send_msg(sock, 'serials', self.get_tsi_cams())
        except Exception as e:
            exc_info = ''.join(
                traceback.format_exception(*sys.exc_info()))
            self.send_msg(sock, 'exception', (str(e), exc_info))

    def process_cam_message(self, sock, msg, value):
        """Handles a message from the camera and passes it on to the client
        if requested.
        """
        if msg == 'cam_closed':
            self.tsi_cam = None
        self.send_msg(sock, msg, value)

    def server_run(self):
        """The mean server thread."""
        timeout = self.timeout

        # Create a TCP/IP socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

        # Connect the socket to the port where the server is listening
        server_address = (self.server, self.port)
        logging.info('ThorCamServer: starting up on {} port {}'
                     .format(*server_address))

        try:
            sock.bind(server_address)
            sock.listen(1)

            r, _, _ = select.select([sock], [], [])
            if not r:
                raise TypeError

            connection, client_address = sock.accept()
            msg_len, msg_buff = (), b''

            try:
                while True:
                    r, _, _ = select.select([connection], [], [], timeout)
                    if r:
                        msg_len, msg_buff, msg, value = self.read_msg(
                            connection, msg_len, msg_buff)
                        if msg and self.process_client_message(
                                connection, msg, value) == 'eof':
                            return

                    try:
                        while self.tsi_cam is not None:
                            msg, value = \
                                self.tsi_cam.from_cam_queue.get_nowait()
                            self.process_cam_message(
                                connection, msg, value)
                    except Empty:
                        pass
            except connection_errors:
                pass
            finally:
                logging.info(
                    'ThorCamServer: closing client connection')
                connection.close()
        except Exception as e:
            logging.exception(e)
        finally:
            if self.tsi_cam is not None:
                self.tsi_cam.send_message('close_cam')
                if self.tsi_cam.camera_thread is not None:
                    self.tsi_cam.camera_thread.join()

            try:
                if self.tsi_sdk is not None:
                    self.tsi_sdk.Dispose()

                if self.tsi_color_sdk is not None:
                    self.tsi_color_sdk.Dispose()
            finally:
                logging.info('ThorCamServer: closing socket')
                sock.close()


if __name__ == '__main__':
    logging.getLogger().setLevel(int(sys.argv[1]))
    server = ThorCamServer(*sys.argv[2:])

    if server.server_thread is not None:
        server.server_thread.join()
