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

class Node:
    def __init__(self, v=1, a=1, s=0):
        self.v = v
        self.a = a
        self.s = s

class FileNode(Node):
    def __init__(self, file, start=None, duration=None, v=1, a=1, s=0, **kwargs):
        super().__init__(v, a, s)
        self.file = file
        self.start = start
        self.duration = duration

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

class SimpleFilterNode(Node):
    def __init__(self, input, filter, *args, type="v", **kwargs):
        super().__init__(input.v, input.a, input.s)
        self.input = input
        self.filter = filter
        self.type = type
        self.args = args
        self.kwargs = kwargs

    def run(self, instance, stream):
        filter = self.filter
        if self.kwargs or self.args:
            filter += "=" + ":".join(
                ["{}={}".format(k, v) for (k, v) in self.kwargs.items()] +
                [str(v) for v in self.args]
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
    def __init__(self, input, scale, *args, **kwargs):
        if isinstance(scale, str):
            if "x" in scale:
                (w, h) = [int(dim) for dim in scale.split("x")]
            else:
                w = int(scale)
                h = int(scale)
        else:
            (w, h) = [int(dim) for dim in scale]
        super().__init__(input, "scale", *args, w=w, h=h)

class ChangeVSpeedNode(SimpleFilterNode):
    def __init__(self, input, speed, *args, **kwargs):
        speed = float(speed)
        expr = "PTS*{}".format(1. / speed)
        super().__init__(input, "setpts", expr)

class ChangeASpeedNode(SimpleFilterNode):
    def __init__(self, input, speed, *args, **kwargs):
        speed = float(speed)
        expr = "PTS*{}".format(1. / speed)
        super().__init__(input, "asetpts", expr, type="a")

def ChangeSpeedNode(input, speed, *args, **kwargs):
    n1 = ChangeVSpeedNode(input, speed, *args, **kwargs)
    n2 = ChangeASpeedNode(n1, speed, *args, **kwargs)
    return n2

def parse(obj):
    if isinstance(obj, str):
        return FileNode(obj)
    elif isinstance(obj, list):
        raise NotImplementedError("Concat not implemented!")
    else:
        if "file" in obj:
            n = FileNode(**obj)
        elif "input" in obj:
            n = parse(obj["input"])
            del obj["input"]
        else:
            raise Exception("Could not find an input")

        if "filter" in obj:
            n = SimpleFilterNode(n, **obj)
        else:
            if "scale" in obj:
                n = ScaleNode(n, obj["scale"])
            if "speed" in obj:
                n = ChangeSpeedNode(n, obj["speed"])
        return n

if __name__ == "__main__":
    fn = sys.argv[1]
    with open(fn) as f:
        obj = yaml.load(f)

    target = "all" if len(sys.argv) < 3 else sys.argv[2]

    part = obj[target]

    ff = FFmpegInstance()

    #inputs = clips_to_input(**part)
    #concat_filter = clips_to_concat_filter(**part)
    if "flags" in obj:
        ff.set_flags(obj["flags"])
    if "output" in obj:
        ff.set_output(obj["output"])
    node = parse(part)
    ff.set_map(node.render(ff))
    ff.run()

    #cmdline = flags + inputs + ["-filter_complex", concat_filter, "-map", "[outv]"] + output

