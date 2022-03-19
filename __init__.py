import os
import subprocess
import json

from fHDHR.exceptions import TunerError


def setup(plugin, versions):

    # Check config for ffmpeg path
    ffmpeg_path = None
    if plugin.config.dict["ffmpeg"]["path"]:
        # verify path is valid
        if os.path.isfile(plugin.config.dict["ffmpeg"]["path"]):
            ffmpeg_path = plugin.config.dict["ffmpeg"]["path"]
        else:
            plugin.logger.warning("Failed to find ffmpeg at %s." % plugin.config.dict["ffmpeg"]["path"])

    if not ffmpeg_path:
        plugin.logger.info("Attempting to find ffmpeg in PATH.")
        if versions.dict["Operating System"]["version"] in ["Linux", "Darwin"]:
            find_ffmpeg_command = ["which", "ffmpeg"]
        elif versions.dict["Operating System"]["version"] in ["Windows"]:
            find_ffmpeg_command = ["where", "ffmpeg"]

        ffmpeg_proc = subprocess.Popen(find_ffmpeg_command, stdout=subprocess.PIPE)
        ffmpeg_path = ffmpeg_proc.stdout.read().decode().strip("\n")
        ffmpeg_proc.terminate()
        ffmpeg_proc.communicate()
        ffmpeg_proc.kill()
        if not ffmpeg_path:
            ffmpeg_path = None
        elif ffmpeg_path.isspace():
            ffmpeg_path = None

        if ffmpeg_path:
            plugin.config.dict["ffmpeg"]["path"] = ffmpeg_path

    if ffmpeg_path:
        ffmpeg_command = [ffmpeg_path, "-version", "pipe:stdout"]
        try:
            ffmpeg_proc = subprocess.Popen(ffmpeg_command, stdout=subprocess.PIPE)
            ffmpeg_version = ffmpeg_proc.stdout.read().decode().split("version ")[1].split(" ")[0]
        except FileNotFoundError:
            ffmpeg_version = None
        except PermissionError:
            ffmpeg_version = None
        finally:
            ffmpeg_proc.terminate()
            ffmpeg_proc.communicate()
            ffmpeg_proc.kill()

    if not ffmpeg_version:
        ffmpeg_version = "Missing"
        plugin.logger.warning("Failed to find ffmpeg.")

    versions.register_version("ffmpeg", ffmpeg_version, "env")


class Plugin_OBJ():

    def __init__(self, fhdhr, plugin_utils, stream_args, tuner):
        self.fhdhr = fhdhr
        self.plugin_utils = plugin_utils
        self.stream_args = stream_args
        self.tuner = tuner
        self.transcode_configs = {}

        self.ffmpeg_path = self.plugin_utils.config.dict["ffmpeg"]["path"]
        self.buffsize = self.plugin_utils.config.dict["ffmpeg"]["buffsize"]

        if self.plugin_utils.versions.dict["ffmpeg"]["version"] == "Missing":
            raise TunerError("806 - Tune Failed: FFMPEG Missing")

    def get(self):

        try:
            cwd = os.path.dirname(os.path.realpath(__file__))
            with open(cwd + '/transcode.json', 'r') as fp:
                self.transcode_configs = json.load(fp)
                self.plugin_utils.logger.noob("Loaded transcoding configurations file, %s" % fp.name)
        except FileNotFoundError:
            self.plugin_utils.logger.noob("Transcoding configurations file not found, using default configuration")
            self.stream_args["transcode_quality"] = None
            self.transcode_configs = {"heavy": { "global": [], "input": [], "output": ["-c", "copy", "-f", "mpegts"]} }

        self.ffmpeg_command = self.ffmpeg_command_assemble(self.stream_args)
        self.plugin_utils.logger.noob("ffmpeg command: %s" % self.ffmpeg_command)
        if self.plugin_utils.config.dict["logging"]["level"].lower() not in ["info", "debug"]:
            errpipe = subprocess.DEVNULL
        else:
            errpipe = None
        ffmpeg_proc = subprocess.Popen(self.ffmpeg_command, stdout=subprocess.PIPE, stderr=errpipe, bufsize=int(self.buffsize))

        def generate():
            try:
                while self.tuner.tuner_lock.locked():

                    chunk = ffmpeg_proc.stdout.read(self.stream_args["bytes_per_read"])
                    if not chunk:
                        break
                    yield chunk

            finally:
                ffmpeg_proc.terminate()
                ffmpeg_proc.communicate()
                ffmpeg_proc.kill()

        return generate()

    def ffmpeg_command_assemble(self, stream_args):
        if stream_args["transcode_quality"]:
            self.plugin_utils.logger.info("Client requested a %s transcode for stream." % stream_args["transcode_quality"])

        if not stream_args["transcode_quality"] in self.transcode_configs:
            self.plugin_utils.logger.info("Trancode type / key not found, forcing 'heavy' transcoding.")
            stream_args["transcode_quality"] = "heavy"

        ffmpeg_command = [self.ffmpeg_path]
        ffmpeg_command.extend(self.global_options(stream_args))
        ffmpeg_command.extend(self.input_options(stream_args))
        ffmpeg_command.extend(["-i", stream_args["stream_info"]["url"]])
        ffmpeg_command.extend(self.ffmpeg_headers(stream_args))
        ffmpeg_command.extend(self.ffmpeg_duration(stream_args))
        ffmpeg_command.extend(self.transcode_profiles(stream_args))
        ffmpeg_command.extend(self.ffmpeg_loglevel())
        ffmpeg_command.extend(["pipe:stdout"])
        return ffmpeg_command

    def global_options(self, stream_args):
        ffmpeg_command = []
        ffmpeg_command.extend(self.transcode_configs[stream_args["transcode_quality"]]["global"])
        return ffmpeg_command

    def input_options(self, stream_args):
        ffmpeg_command = []
        ffmpeg_command.extend(self.transcode_configs[stream_args["transcode_quality"]]["input"])
        return ffmpeg_command

    def ffmpeg_headers(self, stream_args):
        ffmpeg_command = []
        if stream_args["stream_info"]["headers"]:
            headers_string = ""
            if len(list(stream_args["stream_info"]["headers"].keys())) > 1:
                for x in list(stream_args["stream_info"]["headers"].keys()):
                    headers_string += "%s: %s\r\n" % (x, stream_args["stream_info"]["headers"][x])
            else:
                for x in list(stream_args["stream_info"]["headers"].keys()):
                    headers_string += "%s: %s" % (x, stream_args["stream_info"]["headers"][x])
            ffmpeg_command.extend(["-headers", '\"%s\"' % headers_string])
        return ffmpeg_command

    def ffmpeg_duration(self, stream_args):
        ffmpeg_command = []
        if stream_args["duration"]:
            ffmpeg_command.extend(["-t", str(stream_args["duration"])])
        else:
            ffmpeg_command.extend(
                                  [
                                   "-reconnect", "1",
                                   "-reconnect_at_eof", "1",
                                   "-reconnect_streamed", "1",
                                   "-reconnect_delay_max", "2",
                                  ]
                                  )

        return ffmpeg_command

    def ffmpeg_loglevel(self):
        ffmpeg_command = []
        log_level = self.plugin_utils.config.dict["logging"]["level"].lower()

        if log_level in ["noob"]:
            log_level = "info"
        elif log_level in ["ssdp"]:
            log_level = "debug"

        loglevel_dict = {
                        "debug": "debug",
                        "info": "info",
                        "error": "error",
                        "warning": "warning",
                        "critical": "fatal",
                        }
        if log_level not in ["debug"]:
            ffmpeg_command.extend(["-nostats", "-hide_banner"])
        ffmpeg_command.extend(["-loglevel", loglevel_dict[log_level]])
        return ffmpeg_command

    def transcode_profiles(self, stream_args):
        ffmpeg_command = []
        ffmpeg_command.extend(self.transcode_configs[stream_args["transcode_quality"]]["output"])
        return ffmpeg_command
