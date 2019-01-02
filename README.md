# FFedit
Command-line video editing software that is just a thin wrapper around FFmpeg

## Simple example
FFedit takes in a YAML file describing how to cut and splice your video. Here is a very simple example:
```yaml
all:
  - a.mkv
  - b.mkv
```

This will join `a.mkv` and `b.mkv` together and create a file called `out.mkv`.

It will result in the following FFmpeg command:

```
ffmpeg \
    -y -loglevel warning -stats \
    -i a.mkv -i b.mkv -filter_complex '[0:v:0][0:a:0][1:v:0][1:a:0]concat=n=2:v=1:a=1[f0][f1]' -map '[f0]' -map '[f1]' \
    out.mkv
```

To change the output filename or flags, add an `output` section to the recipe.

```yaml
all:
  - a.mkv
  - b.mkv
output:
  ["-preset", "ultrafast", "c.mkv"]
```

You can do the same for the flags at the beginning of the command line by adding a `flags` section.

FFedit builds the `all` target by default. You can specify a second command line argument to build a different target.
You can also build with a different set of `flags` or `output` by specifying `-f` or `-o`.
This is useful for different output qualities or formats, for instance a quick draft render vs a final render.

In order to trim the videos, you need to expand the clip into more verbose form:
```yaml
all:
  - clip:
      file: a.mkv
      start: 1:20 # Start 1 minute 20 seconds in
      duration: 15 # Only take 15 seconds
  - b.mkv
```

You can add a few kinds of filters to the more verbose form:
```yaml
all:
  - clip:
      file: a.mkv
      start: 1:20
      duration: 15
  - clip:
      file: b.mkv
      speed: 2x # speed up video
      tempo: 2x # adjust audio as well
```

Filters can also be added in a specific order:
```yaml
all:
  - clip:
      file: a.mkv
      start: 1:20
      duration: 15
  - clip:
      file: b.mkv
      filters:
        - speed: 4x
        - tempo: 2x # speed up audio
        - tempo: 2x # speed up audio again (FFmpeg limitation workaround)
```

A list is the shorthand form of the `concat` filter. Here is the more verbose form:
```yaml
all:
  concat:
    inputs:
      - clip:
          file: a.mkv
          start: 1:20
          duration: 15
      - clip:
          file: b.mkv
          speed: 2x
```

FFedit is hierarchical. The `concat` object is just like a `clip` object. For instance, you can apply filters to `concat`:
```yaml
all:
  concat:
    fadein: 2 # fade in over 2 seconds
    fadeout: 2 # fade out over 2 seconds
    inputs:
      - clip:
          file: a.mkv
          start: 1:20
          duration: 15
      - clip:
          file: b.mkv
          speed: 2x
          tempo: 2x
```

## More advanced usage

YAML references can be used to create a hierarchical project. You can render only `scene1` by specifying `target` on the command line.

```yaml
scene1: &sceen1
  concat:
    inputs:
      - a.mkv
      - b.mkv
scene2: &sceen2
  concat:
    inputs:
      - c.mkv
      - d.mkv
all:
  concat:
    fadein: 2
    fadeout: 2
    - *scene1
    - *scene2
```

FFedit uses `ffprobe` to deduce how many video streams and audio streams are in a file, and their duration.
It attempts to keep track of these through filters (e.g. the `speed` filter affects the time accordingly.)
But sometimes it will make mistakes, or sometimes you want to ignore a video or audio stream.
You can set keys `v` for number of video streams, `a` for number of audio streams, or `t` for duration explicitly in these cases.

```yaml
all:
  concat:
    a: 0 # Ignore audio track if it exists
    inputs:
      - a.mkv
      - b.mkv
```

The `addaudio` filter can be used to e.g. mix in background muxic:
```yaml
all:
  concat:
    a: 0
    inputs:
      - a.mkv
      - b.mkv
    addaudio:
      audio:
        clip:
          file: ../music.ogg
          start: 3
          duration: 30
```

There is a more verbose form of filters. You may want to use this to make the filtergraph topology more obvious.
```yaml
all:
  scale:
    input: a.mkv # or you could use the more verbose form of a clip, or another filter
    scale: 640x480
    # Note you can't add filters by name (or a filters list) to a filter object (because oh god why)
```

Lastly, you can add existing ffmpeg filters using the generic `filter`. Be sure to set `a`, `v`, or `t` if necessary.
There is a more verbose form of filters using the `filter` object. You may want to use this to make the filtergraph topology more obvious.
```yaml
all:
  clip:
    file: a.mkv
    duration: 6
    a: 0
    filters:
    - filter:
        name: setpts
        type: v # This filter operates on video only (audio will pass through unchanged)
                # (this is the default)
        args: ["0.5 * PTS"]
        t: 3 # Correct for length change so things like fadeout work properly
```

## Usage
```
usage: ffedit recipe [target] [-h] [-C PATH] [-o OUTPUT] [-f FLAGS] [--dry]

Command-line video editing software that is just a thin wrapper around FFmpeg

positional arguments:
  recipe                A YAML file describing how to render the video
  target                Which key in the recipe to render

optional arguments:
  -h, --help            show this help message and exit
  -C PATH, --working-dir PATH
                        Look in this directory for media referenced in the
                        recipe
  -o OUTPUT, --output OUTPUT
                        Which key in the recipe to use for the output command
                        (will use 'output' if unspecified)
  -f FLAGS, --flags FLAGS
                        Which key in the recipe to use for the FFmpeg flags
                        (will use 'flags' if unspecified)
  --dry                 Do not run any FFmpeg commands (FFprobe will still
                        run)
```
