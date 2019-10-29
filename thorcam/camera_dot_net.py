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

import thorcam
from thorcam.camera import yaml_loads, yaml_dumps, connection_errors, \
    EndConnection, ThorCamBase

import clr, System
from System import Array, Int32
from System.Runtime.InteropServices import GCHandle, GCHandleType

import warnings

__all__ = ('TSICamera', 'ThorCamServer')

warnings.simplefilter('ignore', ruamel.yaml.error.MantissaNoDotYAML1_1Warning)

_MAP_NET_NP = {
    'Single' : np.dtype('float32'),
    'Double' : np.dtype('float64'),
    'SByte'  : np.dtype('int8'),
    'Int16'  : np.dtype('int16'),
    'Int32'  : np.dtype('int32'),
    'Int64'  : np.dtype('int64'),
    'Byte'   : np.dtype('uint8'),
    'UInt16' : np.dtype('uint16'),
    'UInt32' : np.dtype('uint32'),
    'UInt64' : np.dtype('uint64'),
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
        if sourceHandle.IsAllocated: sourceHandle.Free()
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
    `exception`, `cam_closed`, `image`, `playing`, `cam_open`, or `settings`.

    See :class:`ThorCamServer` for details.
    """

    camera_thread = None
    """Internal camera thread.
    """

    _freqs_to_str_map = {}

    _str_to_freqs_map = {}

    _taps_to_str_map = {}

    _str_to_taps_map = {}

    def __init__(self, tsi_sdk, tsi_interface, serial):
        self.tsi_sdk = tsi_sdk
        self.tsi_interface = tsi_interface
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
        hw_mode = cam.get_OperationMode() == self.tsi_interface.OperationMode.HardwareTriggered
        d['trigger_type'] = self.supported_triggers[1 if hw_mode else 0]

        rang = cam.get_GainRange()
        d['gain_range'] = rang.Minimum, rang.Maximum
        d['gain'] = cam.get_Gain()

        rang = cam.get_BlackLevelRange()
        d['black_level_range'] = rang.Minimum, rang.Maximum
        d['black_level'] = cam.get_BlackLevel()

        if cam.GetIsDataRateSupported(self.tsi_interface.DataRate.ReadoutSpeed20MHz):
            if cam.GetIsDataRateSupported(self.tsi_interface.DataRate.ReadoutSpeed40MHz):
                d['supported_freqs'] = ['20 MHz', '40 MHz']
            else:
                d['supported_freqs'] = ['20 MHz', ]
        else:
            if cam.GetIsDataRateSupported(self.tsi_interface.DataRate.FPS50):
                d['supported_freqs'] = ['30 FPS', '50 FPS']
            else:
                d['supported_freqs'] = ['30 FPS', ]
        d['freq'] = self._freqs_to_str_map[cam.get_DataRate()]

        if cam.GetIsTapsSupported(self.tsi_interface.Taps.QuadTap):
            d['supported_taps'] = ['1', '2', '4']
        elif cam.GetIsTapsSupported(self.tsi_interface.Taps.DualTap):
            d['supported_taps'] = ['1', '2']
        elif cam.GetIsTapsSupported(self.tsi_interface.Taps.SingleTap):
            d['supported_taps'] = ['1', ]
        else:
            d['supported_taps'] = []

        if cam.GetIsTapsSupported(self.tsi_interface.Taps.SingleTap):
            d['taps'] = self._taps_to_str_map[cam.get_Taps()]
        else:
            d['taps'] = ''

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
            value = max(min(value, self.binning_x_range[1]), self.binning_x_range[0])
            roi_bin = cam.get_ROIAndBin()
            roi_bin.BinX = value
            cam.set_ROIAndBin(roi_bin)
        elif setting == 'binning_y':
            value = max(min(value, self.binning_y_range[1]), self.binning_y_range[0])
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
                self.tsi_interface.OperationMode.HardwareTriggered if hw_mode else
                self.tsi_interface.OperationMode.SoftwareTriggered)
        elif setting == 'trigger_count':
            cam.set_FramesPerTrigger_zeroForUnlimited(max(0, value))
        elif setting == 'frame_queue_size':
            cam.set_MaximumNumberOfFramesToQueue(max(1, value))
        elif setting == 'gain':
            value = int(max(min(value, self.gain_range[1]), self.gain_range[0]))
            cam.set_Gain(value)
        elif setting == 'black_level':
            value = int(max(min(value, self.black_level_range[1]), self.black_level_range[0]))
            cam.set_BlackLevel(value)
        elif setting == 'freq':
            cam.set_DataRate(self._str_to_freqs_map[value])
        elif setting == 'taps' and value:
            cam.set_Taps(self._str_to_taps_map[value])
        elif setting == 'color_gain':
            r, g, b = value
            mat = [r, 0, 0, 0, g, 0, 0, 0, b]
            color_pipeline = self.tsi_interface.ColorPipeline()
            color_pipeline.set_ColorMode(
                self.tsi_interface.ColorMode.StandardRGB)
            color_pipeline.InsertColorTransformMatrix(0, mat)
            color_pipeline.InsertColorTransformMatrix(
                1, cam.GetCameraColorCorrectionMatrix())
            cam.set_ColorPipelineOrNull(color_pipeline)
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
        color = frame.ImageData.NumberOfChannels == 3
        data = as_numpy_array(frame.ImageData.ImageData_monoOrBGR)
        # img = Image(
        #     plane_buffers=[data.tobytes()],
        #     pix_fmt='bgr48le' if color else 'gray16le', size=(w, h))
        return data.tobytes(), 'bgr48le' if color else 'gray16le', (w, h), \
            count, queued_count, t

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
                                        ('settings',
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
                                ('settings', self.write_setting(cam, *value)))

            if cam.IsArmed:
                cam.Disarm()
            cam.Dispose()
        except Exception as e:
            exc_info = ''.join(traceback.format_exception(*sys.exc_info()))
            from_cam_queue.put(('exception', (str(e), exc_info)))

            try:
                if cam is not None:
                    if cam.IsArmed:
                        cam.Disarm()
                    cam.Dispose()
            except:
                pass
        finally:
            from_cam_queue.put(('cam_closed', None))
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
            The settings value. A dict of settings and their values.
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
        from Thorlabs.TSI.TLCamera import TLCameraSDK
        import Thorlabs.TSI.TLCameraInterfaces as tsi_interface
        self.tsi_sdk = TLCameraSDK.OpenTLCameraSDK()
        self.tsi_interface = tsi_interface
        # seems to be needed
        tsi_interface.CameraSensorType

    def get_tsi_cams(self):
        """Returns the list of serial numbers of the cameras attached."""
        cams = self.tsi_sdk.DiscoverAvailableCameras()
        names = []
        for i in range(cams.get_Count()):
            names.append(cams.get_Item(i))
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
                    serial=value)
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
        cam = None

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

            logging.info('ThorCamServer: closing socket')
            sock.close()


if __name__ == '__main__':
    logging.getLogger().setLevel(int(sys.argv[1]))
    server = ThorCamServer(*sys.argv[2:])

    if server.server_thread is not None:
        server.server_thread.join()
