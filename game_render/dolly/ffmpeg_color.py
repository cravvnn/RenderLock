"""FFmpeg's SDR RGB-to-YUV conversion and color signaling for video masters."""

# Tag the frames as well as the output stream: encoders can otherwise retain
# unknown primaries and transfer characteristics from the raw RGB input.
REC709_SCALE = "scale=out_color_matrix=bt709:out_range=tv"
REC709_FRAME_TAGS = (
    "setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709"
)
REC709_FILTER = REC709_SCALE + "," + REC709_FRAME_TAGS
REC709_OUTPUT_ARGS = (
    "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
    "-color_range", "tv",
)
