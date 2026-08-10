"""Core download engine."""

__all__ = ["DownloadController", "download_file", "probe_url", "DownloadState"]


def __getattr__(name: str):
    if name in ("DownloadController", "download_file"):
        from core.download import DownloadController, download_file
        return {"DownloadController": DownloadController, "download_file": download_file}[name]
    if name == "probe_url":
        from core.probe import probe_url
        return probe_url
    if name == "DownloadState":
        from core.state import DownloadState
        return DownloadState
    raise AttributeError(name)
