#!/usr/bin/python3

import yaml
import sys
import subprocess
import shlex

class FFmpegInstance:
    def __init__(self):
        self.ffmpeg = "ffmpeg"
        self.inputs = []
        self.input_n = 0
        self.filter = []
        self.filter_n = 0
        self.flags = ["-y", "-loglevel", "warning", "-stats"]
        self.map = []
        self.output = ["out.mkv"]

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

    def run(self, dry=False):
        filter = []
        if self.filter:
            filter_str = ",".join(self.filter)
            filter = ["-filter_complex", filter_str]
        cmdline = [self.ffmpeg] + self.flags + self.inputs + filter + self.map + self.output
        s = " ".join([shlex.quote(c) for c in cmdline])
        print(s)
        if not dry:
            subprocess.check_call(cmdline)

def get_singleton(obj):
    if isinstance(obj, dict) and len(obj.items()) == 1:
        return tuple(obj.items())[0]
    else:
        raise TypeError("Expected a dict with one element, got {}".format(repr(obj)))

def obj_to_args(obj):
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

class Node:
    def __init__(self, v=1, a=1, s=0, **kwargs):
        self.v = int(v)
        self.a = int(a)
        self.s = int(s)
        for k, v in kwargs.items():
            setattr(self, k, v)

    def reduce_and_render(self, instance):
        return self.render(instance)

class CompoundNode(Node):
    def __init__(self, v=1, a=1, s=0, **kwargs):
        super().__init__(v, a, s, **kwargs)
        self._reduced = False

    def reduce_and_render(self, instance):
        if self._reduced:
            return self.render(instance)

        # This temporary flag prevents infinite recursion
        self._reduced = True
        n = self

        for k in IMPLICIT_FILTERS:
            if hasattr(self, k):
                options = getattr(self, k)
                (args, kwargs) = obj_to_args(options)
                n = FILTERS[k](n, *args, **kwargs)

        if hasattr(self, "filters"):
            for f in self.filters:
                if isinstance(f, str):
                    n = FILTERS[k](n)
                else:
                    (k, options) = get_singleton(f)
                    (args, kwargs) = obj_to_args(options)
                    n = FILTERS[k](n, *args, **kwargs)
        result = n.render(instance)
        self._reduced = False
        return result

class SimpleFilterNode(Node):
    def __init__(self, input, filter, *args, type="v", kwargs=None, **kwargs2):
        super().__init__(input.v, input.a, input.s)
        self.input = input
        self.filter = filter
        self.type = type
        self.args = args
        if kwargs is not None:
            kwargs2.update(kwargs)
        self.kwargs = kwargs2

    def run(self, instance, stream):
        filter = self.filter
        if self.kwargs or self.args:
            filter += "=" + ":".join(
                ["{}={}".format(k, v) for (k, v) in self.kwargs.items()] +
                [str(v) for v in self.args]
            )
        return instance.add_filter([stream], filter)[0]

    def render(self, instance):
        (v, a, s) = self.input.reduce_and_render(instance)
        if "v" in self.type:
            v = [self.run(instance, stream) for stream in v]
        if "a" in self.type:
            a = [self.run(instance, stream) for stream in a]
        if "s" in self.type:
            s = [self.run(instance, stream) for stream in s]
        return (v, a, s)

class ScaleNode(SimpleFilterNode):
    def __init__(self, input, scale, *args, **kwargs):
        if isinstance(scale, str):
            if "x" in scale:
                (w, h) = [int(dim) for dim in scale.split("x")]
            else:
                w = int(scale)
                h = int(scale)
        else:
            (w, h) = [int(dim) for dim in scale]
        super().__init__(input, "scale", *args, w=w, h=h, **kwargs)

class ChangeVSpeedNode(SimpleFilterNode):
    def __init__(self, input, speed, **kwargs):
        speed = float(speed)
        expr = "PTS*{}".format(1. / speed)
        super().__init__(input, "setpts", expr, **kwargs)

class ChangeASpeedNode(SimpleFilterNode):
    def __init__(self, input, speed, **kwargs):
        speed = float(speed)
        expr = "PTS*{}".format(1. / speed)
        super().__init__(input, "asetpts", expr, type="a", **kwargs)

def ChangeSpeedNode(input, speed, **kwargs):
    n1 = ChangeVSpeedNode(input, speed, **kwargs)
    n2 = ChangeASpeedNode(n1, speed, **kwargs)
    return n2

class ClipNode(CompoundNode):
    def __init__(self, file, start=None, duration=None, v=1, a=1, s=0, **kwargs):
        super().__init__(file=file, start=start, duration=duration, v=v, a=a, s=s, **kwargs)

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
    def __init__(self, *args, v=None, a=None, **kwargs):
        if len(args) == 1 and isinstance(args[0], list):
            inputs = args[0]
        elif len(args) >= 1:
            inputs = args
        elif "inputs" in kwargs:
            inputs = kwargs["inputs"]
        else:
            raise TypeError("Could not deduce inputs for {}".format(__class__.__name__))
        inputs = [i if isinstance(i, Node) else parse(i) for i in inputs]
        if v is None:
            v = min([i.v for i in inputs])
        if a is None:
            a = min([i.a for i in inputs])
        super().__init__(v, a, 0) # concat filter doesn't support subtitles
        self.inputs = inputs

    def render(self, instance):
        rendered_inputs = [i.reduce_and_render(instance) for i in self.inputs]

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
        (v, a1, s) = self.input.reduce_and_render(instance)
        (_, a2, _) = self.audio.reduce_and_render(instance)
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
        return ClipNode(obj)
    elif isinstance(obj, list):
        return ConcatNode(*obj)
    elif isinstance(obj, dict) and len(obj.items()) == 1:
        (node_name, options) = get_singleton(obj)
        (args, kwargs) = obj_to_args(options)
        return NODES[node_name](*args, **kwargs)
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
}

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
    ff.set_map(node.reduce_and_render(ff))
    ff.run()

    #cmdline = flags + inputs + ["-filter_complex", concat_filter, "-map", "[outv]"] + output

