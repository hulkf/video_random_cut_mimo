import os

from core.video_normalization_cache import normalize_cached, prepare_cache


def test_reuses_bytes_and_invalidates_changed_content_profile_and_bad_cache(tmp_path):
    source, output = tmp_path / "input.mov", tmp_path / "output.mp4"
    source.write_bytes(b"first")
    cache = prepare_cache(tmp_path / "cache")
    conversions = []

    def convert(src, dst):
        conversions.append(src.read_bytes())
        dst.write_bytes(b"video:" + src.read_bytes())

    def validate(file):
        return file.read_bytes().startswith(b"video:")

    assert not normalize_cached(cache, source, output, "portrait-6", convert, validate)
    assert normalize_cached(cache, source, output, "portrait-6", convert, validate)
    next(cache.glob("*.mp4")).write_bytes(b"corrupt")
    assert not normalize_cached(cache, source, output, "portrait-6", convert, validate)

    stat = source.stat()
    source.write_bytes(b"other")
    os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert not normalize_cached(cache, source, output, "portrait-6", convert, validate)
    assert not normalize_cached(cache, source, output, "portrait-8", convert, validate)
    assert conversions == [b"first", b"first", b"other", b"other"]
