import logging
logging.basicConfig(level=logging.DEBUG)
from video_slicer import slice_video
path, sha, size = slice_video("test123")
print(f"path={path} sha={sha} size={size}")