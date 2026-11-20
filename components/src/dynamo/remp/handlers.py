    ``torch -> numpy.tobytes -> base64`` keeps the heavy work in C; output bytes
    are identical to the ``struct``-based path on little-endian hosts.
    """
    if isinstance(data, torch.Tensor):
        vec = _flatten_pooling_tensor(data)
        return base64.b64encode(vec.tobytes()).decode("ascii")
    # Fallback for non-tensor pooling outputs (rare): reuse the list path.
    return _encode_floats_to_base64(_pooling_output_to_list(data))
