class FakeClient:
    """Records write calls instead of talking to qBittorrent."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        if name.startswith("torrents_"):
            return lambda **kwargs: self.calls.append((name, kwargs))
        raise AttributeError(name)


class FakeFS:
    def __init__(self, files: dict[str, int]):
        self.files = files

    def walk(self, root):
        for path, size in self.files.items():
            if path.casefold().startswith(root.casefold() + "\\"):
                yield path, size

    def exists(self, path):
        return path in self.files
