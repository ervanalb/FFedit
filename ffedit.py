#!/usr/bin/python3

import yaml
import sys
import subprocess
import shlex

class FFmpegInstance:
    def __init__(self):
        self.ffmpeg = "ffmpeg"
        self.ffprobe = "ffprobe"
        self.inputs = []
        self.input_n = 0
        self.filter = []
        self.filter_n = 0
        self.flags = ["-y", "-loglevel", "warning", "-stats"]
        self.ffprobe_flags = ["-loglevel", "warning", "-hide_banner", "-show_streams"]
        self.map = []
        self.output = ["out.mkv"]
        self.dry = False

    def add_input(self, i):
        self.inputs += i
        n = self.input_n
        self.input_n += 1
        return str(n)

    def add_filter(self, inputs, filt, n_outputs=1):
        def maybe_add_brackets(s):
            s = str(s)
            if s.startswith("[") and s.endswith("]"):
                return s
            return "[" + s + "]"

        outputs = []
        for i in range(n_outputs):
            outputs.append("[f{}]".format(self.filter_n))
            self.filter_n += 1
        input_str = "".join(maybe_add_brackets(i) for i in inputs)
        output_str = "".join(outputs)
        self.filter.append(input_str + filt + output_str)
        return outputs

    def set_flags(self, flags):
        self.flags = flags

    def set_map(self, rendered_node):
        (v, a, s) = rendered_node
        for stream in v + a + s:
            self.map += ["-map", "{}".format(stream)]

    def set_output(self, output):
        self.output = output

    def run(self):
        filter = []
        if self.filter:
            filter_str = ",".join(self.filter)
            filter = ["-filter_complex", filter_str]
        cmdline = [self.ffmpeg] + self.flags + self.inputs + filter + self.map + self.output
        s = " ".join([shlex.quote(c) for c in cmdline])
        print(s)
        if self.dry:
            return
        subprocess.check_call(cmdline)

    def analyze_file(self, file):
        cmdline = [self.ffprobe] + self.ffprobe_flags + [file]
        s = " ".join([shlex.quote(c) for c in cmdline])
        print(s)
        result = str(subprocess.check_output(cmdline), encoding="latin1")
        lines = result.split("\n")
        streams = []
        cur_stream = {}
        cur_tag = None
        for l in lines:
            if not l:
                continue
            if cur_tag is None:
                if l == "[STREAM]":
                    cur_tag = l
                else:
                    raise ValueError("Could not parse ffprobe output (expected [STREAM], got {})".format(l))
            else:
                if l == "[/STREAM]":
                    cur_tag = None
                    streams.append(cur_stream)
                    cur_stream = {}
                else:
                    (k, v) = l.split("=")
                    cur_stream[k] = v
        if cur_tag is not None:
            raise ValueError("Could not parse ffprobe output (missing [/STREAM])")
        video_streams = [s for s in streams if s.get("codec_type") == "video"]
        audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
        # TODO subtitles?
        result = {}
        result["v"] = len(video_streams)
        result["a"] = len(audio_streams)
        result["s"] = 0
        durations = [float(s["duration"]) for s in video_streams + audio_streams if "duration" in s]
        if durations:
            result["t"] = max(durations)
        return result


def get_singleton(obj):
    if isinstance(obj, dict) and len(obj.items()) == 1:
        return tuple(obj.items())[0]
    else:
        raise TypeError("Expected a dict with one element, got {}".format(repr(obj)))

def ensure_node(obj):
    return obj if isinstance(obj, Node) else parse(obj)

def parse_time(t):
    return float(t)

class Node:
    @classmethod
    def obj_to_args(cls, obj):
        args = []
        kwargs = {}
        if isinstance(obj, str) or isinstance(obj, float) or isinstance(obj, int):
            args = [obj]
        elif isinstance(obj, list):
            args = obj
        elif isinstance(obj, dict):
            kwargs = obj
        else:
            raise TypeError("Expected scalar, list, or dict, but got {}".format(repr(obj)))
        return (args, kwargs)

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            if v is not None:
                setattr(self, k, v)

    @classmethod
    def parse(cls, options, *args, **kwargs):
        (pargs, pkwargs) = cls.obj_to_args(options)
        pargs = args + tuple(pargs)
        pkwargs.update(kwargs)
        print(pargs, pkwargs)
        return cls(*pargs, **pkwargs)

class CompoundNode(Node):
    @classmethod
    def parse(cls, obj):
        node = super().parse(obj)
        for k in IMPLICIT_FILTERS:
            if hasattr(node, k):
                options = getattr(node, k)
                node = FILTERS[k].parse(options, node)

        if hasattr(node, "filters"):
            for f in node.filters:
                if isinstance(f, str):
                    n = FILTERS[k].parse(n)
                else:
                    (k, options) = get_singleton(f)
                    node = FILTERS[k].parse(options, node)
        return node

class SimpleFilterNode(Node):
    def __init__(self, input, name, args=None, kwargs=None, type="v", **kwargs2):
        super().__init__(input=input, name=name, args=args, kwargs=kwargs, type=type, **kwargs2)

    def analyze(self, instance):
        self.input.analyze(instance)
        for attr in ("v", "a", "t"):
            if hasattr(self.input, attr) and not hasattr(self, attr):
                setattr(self, attr, getattr(self.input, attr))

    def run(self, instance, stream):
        filter = self.name
        args = self.args if hasattr(self, "args") else []
        kwargs = self.kwargs if hasattr(self, "kwargs") else {}
        if args or kwargs:
            filter += "=" + ":".join(
                ["{}={}".format(k, v) for (k, v) in kwargs.items()] +
                [str(v) for v in args]
            )
        return instance.add_filter([stream], filter)[0]

    def render(self, instance):
        (v, a, s) = self.input.render(instance)
        if "v" in self.type:
            v = [self.run(instance, stream) for stream in v]
        if "a" in self.type:
            a = [self.run(instance, stream) for stream in a]
        if "s" in self.type:
            s = [self.run(instance, stream) for stream in s]
        return (v, a, s)

class ScaleNode(SimpleFilterNode):
    def __init__(cls, input, scale, **kwargs):
        if isinstance(scale, str):
            if "x" in scale:
                (w, h) = [int(dim) for dim in scale.split("x")]
            else:
                w = int(scale)
                h = int(scale)
        else:
            (w, h) = [int(dim) for dim in scale]
        super().__init__(input, "scale", args=(w, h), **kwargs)

class ChangeVSpeedNode(SimpleFilterNode):
    def __init__(self, input, speed, **kwargs):
        speed = float(speed)
        expr = "PTS*{}".format(1. / speed) # TODO this shouldn't be done in init?
        super().__init__(input, "setpts", args=(expr,), **kwargs)

class ChangeASpeedNode(SimpleFilterNode):
    def __init__(self, input, speed, **kwargs):
        speed = float(speed)
        expr = "PTS*{}".format(1. / speed)
        super().__init__(input, "asetpts", args=(expr,), type="a", **kwargs)

class ChangeSpeedNode(Node): # TODO get rid of VSpeed and ASpeed
    def __init__(self, input, speed, **kwargs):
        self.speed = speed
        self.n1 = ChangeVSpeedNode(input, speed, **kwargs)
        self.n2 = ChangeASpeedNode(self.n1, speed, **kwargs)

    def analyze(self, instance):
        self.n2.analyze(instance)
        for attr in ("v", "a", "t"):
            if hasattr(self.n2, attr) and not hasattr(self, attr):
                setattr(self, attr, getattr(self.n2, attr))

        self.t = self.t / self.speed

    def render(self, instance):
        return self.n2.render(instance)

class ClipNode(CompoundNode):
    def __init__(self, file, start=None, duration=None, **kwargs):
        if start is not None:
            start = parse_time(start)
        if duration is not None:
            duration = parse_time(duration)
        super().__init__(file=file, start=start, duration=duration, **kwargs)

    def analyze(self, instance):
        info = instance.analyze_file(self.file)
        for (k, v) in info.items():
            if not hasattr(self, k):
                setattr(self, k, v)

        if hasattr(self, "start"):
            if hasattr(self, "duration"):
                self.t = min(self.duration, self.t - self.start)
            else: # duration is None
                self.t = self.t - self.start
        else: # start is None
            if hasattr(self, "duration"):
                self.t = min(self.duration, self.t)

    def to_input_cmdline(self):
        cmdline = ["-i", self.file]
        if self.duration is not None:
            cmdline = ["-t", str(self.duration)] + cmdline
        if self.start is not None:
            cmdline = ["-ss", str(self.start)] + cmdline
        return cmdline

    def render(self, instance):
        stream_n = instance.add_input(self.to_input_cmdline())
        def gen_stream_names(type, count):
            return ["{}:{}:{}".format(stream_n, type, i) for i in range(count)]
        return (gen_stream_names("v", self.v),
                gen_stream_names("a", self.a),
                gen_stream_names("s", self.s))

class ConcatNode(CompoundNode):
    def __init__(self, *args, **kwargs):
        if len(args) == 1 and isinstance(args[0], list):
            inputs = args[0]
        elif len(args) >= 1:
            inputs = args
        elif "inputs" in kwargs:
            inputs = kwargs["inputs"]
            del kwargs["inputs"]
        else:
            raise TypeError("Could not deduce inputs for {}".format(__class__.__name__))
        inputs = [ensure_node(i) for i in inputs]
        super().__init__(inputs=inputs, **kwargs)

    def analyze(self, instance):
        for i in self.inputs:
            i.analyze(instance)
        if not hasattr(self, "v"):
            self.v = min([i.v for i in self.inputs])
        if not hasattr(self, "a"):
            self.a = min([i.a for i in self.inputs])
        if not hasattr(self, "t"):
            self.t = sum([i.t for i in self.inputs])

    def render(self, instance):
        rendered_inputs = [i.render(instance) for i in self.inputs]

        stream_inputs = []
        for (v, a, s) in rendered_inputs:
            stream_inputs += v[0:self.v]
            stream_inputs += a[0:self.a]

        filter="concat=n={}:v={}:a={}".format(len(self.inputs), self.v, self.a)

        outputs = instance.add_filter(stream_inputs, filter, self.v + self.a)
        v_outputs = outputs[0:self.v]
        a_outputs = outputs[self.v:]
        return (v_outputs, a_outputs, [])

class AddAudioNode(Node):
    def __init__(self, input, audio, *args, **kwargs):
        if audio.a == 0 or (input.a > 0 and audio.a != 1 and audio.a != input.a):
            raise Exception("Bad number of audio tracks to mix in")
        super().__init__(input.v, max(audio.a, input.a), input.s)
        self.input = input
        self.audio = audio
        self.args = args
        self.kwargs = kwargs

    def render(self, instance):
        (v, a1, s) = self.input.render(instance)
        (_, a2, _) = self.audio.render(instance)
        if len(a1) == 0: # If no existing audio, simply add the new audio tracks
            return (v, a2, s)
        filter = "amix=2"
        if self.kwargs or self.args:
            filter += ":" + ":".join(
                ["{}={}".format(k, v) for (k, v) in self.kwargs.items()] +
                [str(v) for v in self.args]
            )
        if len(a1) == len(a2): # Element-wise mixing
            a_out = [instance.add_filter([t1, t2], filter)[0] for (t1, t2) in zip(a1, a2)]
            return (v, a_out, s)
        # Mix the same audio track with every input track
        a_out = [instance.add_filter([stream, a2[0]], filter)[0] for stream in a1]
        return (v, a_out, s)

def parse(obj):
    if isinstance(obj, str):
        return ClipNode.parse(obj)
    elif isinstance(obj, list):
        return ConcatNode.parse(obj)
    elif isinstance(obj, dict) and len(obj.items()) == 1:
        (node_name, options) = get_singleton(obj)
        return NODES[node_name].parse(options)
    else:
        raise TypeError(obj)

IMPLICIT_FILTERS = [
    "scale",
    "speed"
]

NODES = {
    "clip": ClipNode,
    "concat": ConcatNode,
}

FILTERS = {
    "scale": ScaleNode,
    "speed": ChangeSpeedNode,
    "filter": SimpleFilterNode,
}

NODES.update(FILTERS)

if __name__ == "__main__":
    fn = sys.argv[1]
    with open(fn) as f:
        obj = yaml.load(f)

    target = "all" if len(sys.argv) < 3 else sys.argv[2]

    part = obj[target]

    ff = FFmpegInstance()

    if "flags" in obj:
        ff.set_flags(obj["flags"])
    if "output" in obj:
        ff.set_output(obj["output"])
    node = parse(part)
    node.analyze(ff)
    outputs = node.render(ff)
    print("Output duration is", node.t)
    ff.set_map(outputs)
    ff.run()

    #cmdline = flags + inputs + ["-filter_complex", concat_filter, "-map", "[outv]"] + output

