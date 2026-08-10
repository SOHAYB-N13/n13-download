"""Batch download helpers."""

__all__ = [
    "batch_download_by_pattern",
    "scan_pattern_urls",
    "import_urls_from_file",
    "load_url_list",
    "save_links_to_file",
]

def __getattr__(name: str):
    if name in ("batch_download_by_pattern", "scan_pattern_urls"):
        from batch.pattern import batch_download_by_pattern, scan_pattern_urls
        return {"batch_download_by_pattern": batch_download_by_pattern, "scan_pattern_urls": scan_pattern_urls}[name]
    if name in ("import_urls_from_file", "load_url_list", "save_links_to_file"):
        from batch.sources import import_urls_from_file, load_url_list, save_links_to_file
        return {
            "import_urls_from_file": import_urls_from_file,
            "load_url_list": load_url_list,
            "save_links_to_file": save_links_to_file,
        }[name]
    raise AttributeError(name)
