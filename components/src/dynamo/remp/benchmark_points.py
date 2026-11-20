    try:
        return BenchmarkPoints.model_validate_json(Path(path).read_bytes())
    except Exception as error:
        raise ValueError(f"--benchmark-points-file {path!r}: {error}") from error
