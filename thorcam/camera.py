"""Thor camera interface
========================

The client Thor camera interface. It runs :mod:`thorcam.camera_dot_net` as
a separate process and sends and receives from it all camera requests, such
as configuration and images.

:class:`ThorCam` provides the basic implementation for interacting with the
camera. :class:`ThorCam` provides a example implementation using
:class:`ThorCam` that interacts with the camera.

If the Thor .NET dlls are not provided in their default path, or to
overwrite them with a different version, see
:attr:`ThorCamClient.thor_bin_path`.

"""
import subprocess
import struct
import select
import traceback
from queue import Queue, Empty
from time import perf_counter as clock
import os
import sys
from threading import Thread
from io import StringIO
import socket
import logging
from ruamel.yaml import YAML
import thorcam
import ruamel.yaml

from ffpyplayer.pic import Image

import warnings
warnings.simplefilter('ignore', ruamel.yaml.error.MantissaNoDotYAML1_1Warning)

__all__ = ('yaml_dumps', 'yaml_loads', 'EndConnection', 'connection_errors',
           'ThorCamBase', 'ThorCamClient', 'ThorCam')


def yaml_dumps(value):
    """Encodes the value using yaml and returns it as string.
    """
    yaml = YAML(typ='safe')
    s = StringIO()
    yaml.preserve_quotes = True
    yaml.dump(value, s)
    return s.getvalue()


def yaml_loads(value):
    """Decodes the string representing a yaml value
    and returns the original objects."""
    yaml = YAML(typ='safe')
    return yaml.load(value)


class EndConnection(Exception):
    """Class that represents connection exceptions raised by thorcam package.
    """
    pass


connection_errors = (
    EndConnection, ConnectionAbortedError, ConnectionResetError)
"""Tuple of possible connections errors that may be raised, so we can catch
all of them.
"""


class ThorCamBase(object):
    """Base class with all the config options that the scientific Thor cams
    may support.
    """

    supported_freqs = ['20 MHz', ]
    """The supported frequencies."""

    freq = '20 MHz'
    """The frequency to use."""

    supported_taps = ['1', ]
    """The supported taps."""

    taps = '1'
    """The tap to use."""

    supports_color = False
    """Whether the camera supports color."""

    exposure_range = [0, 100]
    """The supported exposure range in ms."""

    exposure_ms = 5
    """The exposure value in ms to use."""

    binning_x_range = [0, 0]
    """The supported exposure range."""

    binning_x = 0
    """The x binning value to use."""

    binning_y_range = [0, 0]
    """The supported exposure range."""

    binning_y = 0
    """The y binning value to use."""

    sensor_size = [0, 0]
    """The size of the sensor in pixels."""

    roi_x = 0
    """The x start position of the ROI in pixels."""

    roi_y = 0
    """The y start position of the ROI in pixels."""

    roi_width = 0
    """The width after the x start position of the ROI in pixels, to use."""

    roi_height = 0
    """The height after the y start position of the ROI in pixels, to use."""

    gain_range = [0, 100]
    """The supported exposure range."""

    gain = 0
    """The gain value to use."""

    black_level_range = [0, 100]
    """The supported exposure range."""

    black_level = 0
    """The black level value to use."""

    frame_queue_size = 1
    """The max number of image frames to be allowed on the camera's hardware
    queue. Once exceeded, the frames are dropped."""

    supported_triggers = ['SW Trigger', 'HW Trigger']
    """The trigger types supported by the camera."""

    trigger_type = 'SW Trigger'
    """The trigger type of the camera to use."""

    trigger_count = 1
    """The number of frames to capture in response to the trigger."""

    num_queued_frames = 0
    """The number of image frames currently on the camera's hardware queue."""

    color_gain = [1, 1, 1]
    """The color gain for each red, green, and blue channel."""

    settings = [
        'exposure_ms', 'binning_x', 'binning_y', 'roi_x', 'roi_y', 'roi_width',
        'roi_height', 'trigger_type', 'trigger_count', 'frame_queue_size',
        'gain', 'black_level', 'freq', 'taps', 'color_gain']
    """All the possible settings that the camera may support.
    """

    play_settings = ['exposure_ms', 'gain', 'black_level', 'color_gain']
    """All the settings that can be set while the camera is playing.
    """


class ThorCamClient(ThorCamBase):
    """The Thor camera client interface. This is the class to be used to
    control the camera.
    """

    server = 'localhost'
    """The server address to use to start the internal server.
    """

    port = None
    """The server port to open in the internal server. When None, we find
    a not in use port.
    """

    timeout = 0.01
    """How long to wait wait on network requests, before checking the
    queues.
    """

    thor_bin_path = thorcam.dep_bins[0] if thorcam.dep_bins else ''
    """The full path to where the Thor .NET binaries are located.

    We use this path to locate and load the .NET interface to the camera.
    This path must contain at least the following two dlls:
    ``Thorlabs.TSI.TLCamera.dll`` and ``Thorlabs.TSI.TLCameraInterfaces.dll``.

    It defaults to the path in :attr:`thorcam.dep_bins` because that's where
    the python wheel stores the dlls.
    """

    _server_thread = None

    to_server_queue = None
    """The queue we use to send requests to the server.
    """

    _client_thread = None

    def _get_open_port(self):
        """Returns a available unused open port on the localhost.
        """
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("", 0))
        s.listen(1)
        port = s.getsockname()[1]
        s.close()
        return port

    def start_cam_process(self):
        """Starts the server in its own process.
        This must be called before any camera operations can start.
        """
        if self._server_thread is not None:
            return

        if self.port is None:
            self.port = self._get_open_port()

        thread = self._server_thread = Thread(target=self.cam_process)
        thread.start()

        to_server_queue = self.to_server_queue = Queue()
        thread = self._client_thread = Thread(
            target=self.client_run, args=(to_server_queue, ))
        thread.start()

    def process_exited(self, e=None, exc_info=None):
        """Called internally when the camera server process exits.

        :param e: If provided, the error with which the process exited.
        :param exc_info: If provided, the stderr string of the process.
        """
        self._server_thread = None
        if e:
            self.handle_exception(e, exc_info)

    def client_exited(self):
        """Called internally when the client thread exits.
        """
        self._client_thread = None

    def cam_process(self):
        """The thread that runs the camera server process.
        """
        script = os.path.join(os.path.dirname(__file__), 'camera_dot_net.py')
        try:
            subprocess.run(
                [sys.executable, script,
                 str(logging.getLogger().getEffectiveLevel()),
                 self.thor_bin_path, self.server, str(self.port),
                 str(self.timeout)],
                stderr=subprocess.PIPE, stdout=sys.stdout, check=True,
                universal_newlines=True)
        except subprocess.CalledProcessError as e:
            exc_info = e.stderr
            self.process_exited(e, exc_info)
        except Exception as e:
            exc_info = ''.join(
                traceback.format_exception(*sys.exc_info()))
            self.process_exited(e, exc_info)
        else:
            self.process_exited()

    def create_image_from_msg(self, msg_value):
        """Takes the ``value`` from the server that contains image data,
        constructs the image and returns it and its metadata.

        It returns a 4-tuple of ``img, count, queued_count, t)``. Where
        ``img`` is the image. ``count`` is the image number as provided by the
        camera. ``queued_count`` is the number of frames the camera still has
        to process (i.e. need to be send from the hardware). ``t`` is the
        image timestamp.
        """
        data, fmt, (w, h), count, queued_count, t = msg_value
        img = Image(plane_buffers=[data], pix_fmt=fmt, size=(w, h))
        return img, count, queued_count, t

    def received_camera_response(self, msg, value):
        """Called by the client thread to handle a message received from the
        server. Subclass should overwrite this to handle the messages.

        :param msg: A string with the message name
        :param value: The message content.
        """
        raise NotImplementedError

    def handle_exception(self, e, exc_info):
        """Called by the client thread when an exception happens in the server
        or client.

        :param e: The error.
        :param exc_info: The exc_info represented as a string.
        """
        raise NotImplementedError

    def send_camera_request(self, msg, value=None):
        """Sends a request to the server and camera.

        See :class:`thorcam.camera_dot_net.ThorCamServer` for messages that the
        client may send to the server.

        :param msg: The message name.
        :param value: The message value to be sent.
        """
        self.to_server_queue.put((msg, value))

    def client_run(self, to_server_queue):
        """The thread that runs the client connection to the server.
        """
        timeout = self.timeout

        # Create a TCP/IP socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

        # Connect the socket to the port where the server is listening
        server_address = (self.server, self.port)
        logging.info('ThorCam: connecting to {} port {}'
                     .format(*server_address))

        msg_len, msg_buff = (), b''

        try:
            ts = clock()
            while True:
                try:
                    sock.connect(server_address)
                    break
                except ConnectionRefusedError:
                    if clock() - ts > 5:
                        raise

            done = False

            while not done:
                r, _, _ = select.select([sock], [], [], timeout)
                if r:
                    msg_len, msg_buff, msg, value = self.read_msg(
                        sock, msg_len, msg_buff)
                    if msg:
                        self.received_camera_response(msg, value)

                try:
                    while True:
                        msg, value = to_server_queue.get_nowait()
                        self.send_msg(sock, msg, value)
                        if msg == 'eof':
                            done = True
                            break
                except Empty:
                    pass
        except Exception as e:
            exc_info = ''.join(traceback.format_exception(*sys.exc_info()))
            self.handle_exception(e, exc_info)
        finally:
            logging.info('ThorCam: closing socket')
            try:
                sock.close()
            finally:
                self.client_exited()

    def send_msg(self, sock, msg, value):
        """Sends message to the server."""
        data = yaml_dumps((msg, value))
        data = data.encode('utf8')

        sock.sendall(struct.pack('>II', len(data), 0))
        sock.sendall(data)

    def decode_data(self, msg_buff, msg_len):
        """Decodes binary message from the server."""
        n, bin_n = msg_len
        assert n + bin_n == len(msg_buff)
        data = msg_buff[:n].decode('utf8')
        msg, value = yaml_loads(data)

        if msg == 'image':
            bin_data = msg_buff[n:]
            value = bin_data, *value
        else:
            assert not bin_n
        return msg, value

    def read_msg(self, sock, msg_len, msg_buff):
        """Reads data from the server and decodes it."""
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

    def stop_cam_process(self, join=False):
        """Requests that the server and client threads/process close.
        If ``join``, we block here until the threads/process exit.
        """
        self.send_camera_request('eof')
        if join:
            client = self._client_thread
            process_thread = self._server_thread
            if client is not None:
                client.join()
            if process_thread is not None:
                process_thread.join()


class ThorCam(ThorCamClient):
    """A example implementation of :class:`ThorCamClient` that handles the
    messages and provides user friendly methods to interact with the camera.
    """

    serials = []
    """After requesting the list of available camera serials, it is stored here
    upon the server response in :meth:`received_camera_response`.
    """

    cam_open = False
    """After requesting that a camera be opened/closed, when the server
    responds that it is open/closed in :meth:`received_camera_response` this
    is set here.
    """

    cam_playing = False
    """After requesting that a camera start/stop playing, when the server
    responds that it is/is not playing in :meth:`received_camera_response` this
    is set here.
    """

    def received_camera_response(self, msg, value):
        if msg == 'cam_open':
            self.cam_open = True
        elif msg == 'cam_closed':
            self.cam_playing = False
            self.cam_open = False
        elif msg == 'playing':
            self.cam_playing = value
        elif msg == 'settings':
            for key, val in value.items():
                setattr(self, key, val)
        elif msg == 'serials':
            self.serials = value
        elif msg == 'exception':
            self.handle_exception(*value)
        elif msg == 'image':
            self.got_image(*self.create_image_from_msg(value))

    def got_image(self, image, count, queued_count, t):
        """Called when we get an image from the server.

        :param image: The image object
        :param count: The frame number starting from 0 as given by the camera.
        :param queued_count: How many more frames are on the hardware queue.
        :param t: The frame timestamp.
        """
        pass

    def handle_exception(self, e, exc_info):
        logging.error(e)
        if exc_info:
            logging.error(exc_info)

    def open_camera(self, serial):
        """Requests that the camera be opened.

        :param serial: The camera's serial number string.
        """
        self.send_camera_request('open_cam', serial)

    def close_camera(self):
        """Requests that the camera be closed.
        """
        self.send_camera_request('close_cam', None)

    def refresh_cameras(self):
        """Requests that we be sent the list of attached cameras (their serial
        numbers).
        """
        self.send_camera_request('serials', None)

    def play_camera(self):
        """Requests that the camera start playing frames.
        """
        self.send_camera_request('play', None)

    def stop_playing_camera(self):
        """Requests that the camera stop playing frames.
        """
        self.send_camera_request('stop', None)

    def set_setting(self, name, value):
        """Requests that the setting should be changed for the camera.

        :param name: The setting name to be changed. E.g. ``"exposure_ms"``.
        :param value: The new setting value.
        """
        self.send_camera_request('setting', (name, value))
